import json
import logging
from collections import Counter
from json import JSONDecodeError

from django.core.signing import TimestampSigner
from django.db import transaction
from django.http import HttpRequest, JsonResponse
from idempotency_key.decorators import idempotency_key
from paypalserversdk.exceptions.api_exception import ApiException

from registration import mqtt, tasks
from registration.forms import OrderForm
from registration.models import (
    Attendee,
    Cart,
    DealerAsst,
    Decimal,
    Discount,
    Event,
    Order,
    OrderItem,
    PriceLevel,
    PriceLevelOption,
    settings,
)
from registration.payments import (
    charge_payment,
    update_capacity_for_status_change,
)
from registration.paypal_payments import (
    capture_paypal_payment,
    create_unpaid_paypal_order,
)
from registration.types import BillingData, TranslatedCartItem

from . import cart, common

logger = logging.getLogger(__name__)


def _count_price_levels(cartItems, orderItems):
    """Count items per price level from cart items and/or order items."""
    counts = Counter()
    if cartItems:
        for item in cartItems:
            post_data = json.loads(item.formData)
            pl_id = post_data["priceLevel"]["id"]
            counts[pl_id] += 1
    if orderItems:
        for item in orderItems:
            counts[item.priceLevel.id] += 1
    return counts


def _check_capacity(price_level_counts):
    """Check capacity for all price levels. Must be called inside transaction.atomic().

    Uses select_for_update() to lock rows and prevent TOCTOU overselling.
    Returns (levels, error_message) where levels is a list of (PriceLevel, count)
    tuples on success, or None on failure.
    """
    sold_out = []
    reserved = []
    levels = []
    for pl_id, count in price_level_counts.items():
        level = PriceLevel.objects.select_for_update().get(id=pl_id)
        result = level.check_capacity_available(count)
        if result == PriceLevel.CAPACITY_SOLD_OUT:
            sold_out.append(level.name)
        elif result == PriceLevel.CAPACITY_RESERVED:
            reserved.append(level.name)
        else:
            levels.append((level, count))

    if sold_out or reserved:
        parts = []
        if sold_out:
            names = ", ".join(sold_out)
            verb = "is" if len(sold_out) == 1 else "are"
            parts.append(f"{names} {verb} sold out")
        if reserved:
            names = ", ".join(reserved)
            parts.append(
                f"All slots for {names} are currently reserved by pending "
                "payments. Some may become available shortly — please try again "
                "in a minute."
            )
        return None, ". ".join(parts)

    return levels, None


def _save_order_items(order, cartItems, orderItems):
    """Save cart items or order items linked to an order."""
    if cartItems:
        for item in cartItems:
            order_item = cart.saveCart(item)
            order_item.order = order
            order_item.save()
    elif orderItems:
        for order_item in orderItems:
            order_item.order = order
            order_item.save()


def get_cart_data_from_session(
    request: HttpRequest,
) -> tuple[list[Cart], list[OrderItem], str]:
    """
    Retrieve cart data from the Django session.

    :param request: The request sent by the client.
    :return: A tuple consisting of:

        * A list of Cart models
        * A list of OrderItem models
        * The discount code applied to the cart, if any
    """

    cart_ids = request.session.get("cart_items", [])
    order_ids = request.session.get("order_items", [])
    return (
        list(Cart.objects.filter(id__in=cart_ids)),
        list(OrderItem.objects.filter(id__in=order_ids)),
        request.session.get("discount", ""),
    )


def do_checkout(
    processor: str,
    billingData: BillingData,
    total: Decimal,
    discount: Decimal,
    cartItems: list,
    orderItems: list,
    donationOrg: Decimal,
    donationCharity: Decimal,
    request: HttpRequest = None,
) -> tuple[bool, dict, Order]:
    event = Event.objects.get(default=True)
    # Reuse the reference token set on the PayPal order at create_paypal_order
    # time so that invoice_id/custom_id on every downstream PayPal webhook
    # resolves to this Order via Order.reference. Fall back to a fresh token
    # when called outside the HTTP flow (e.g. direct unit tests).
    reference = None
    if processor == "paypal" and request is not None:
        reference = request.session.get("pending_paypal_reference")
    if not reference:
        reference = common.get_unique_confirmation_token(Order)

    billName = None
    if billingData.get("cc_firstname") and billingData.get("cc_lastname"):
        billName = f"{billingData.get('cc_firstname')} {billingData.get('cc_lastname')}"
    form = OrderForm(
        collect_billing_address=processor == "square" and event.collectBillingAddress,
        data={
            "total": Decimal(total),
            "reference": reference,
            "discount": discount,
            "orgDonation": donationOrg,
            "charityDonation": donationCharity,
            "billingName": billName,
            "billingAddress1": billingData.get("address1"),
            "billingAddress2": billingData.get("address2"),
            "billingCity": billingData.get("city"),
            "billingState": billingData.get("state"),
            "billingCountry": billingData.get("country"),
            "billingEmail": billingData.get("email"),
            "billingPostal": billingData.get("postal"),
        },
    )

    if not form.is_valid():
        return False, {"errors": [{"code": f"{k} - {v}"} for k, v in form.errors]}, None

    order: Order = form.save(commit=False)

    price_level_counts = _count_price_levels(cartItems, orderItems)

    try:
        # Reserve capacity and persist a PENDING Order + cart items up
        # front. This matches the limited-registration-stock design: the
        # Attendee/Badge/OrderItem rows exist as soon as the reservation
        # is held, so capacity counters and DB rows stay consistent even
        # when payment later fails.
        with transaction.atomic():
            levels, error = _check_capacity(price_level_counts)
            if error:
                return False, error, None
            for level, count in levels:
                level.reserve_slots(count)
            order.status = Order.PENDING
            order.save()
            _save_order_items(order, cartItems, orderItems)

        # Payment dispatch. charge_payment / capture_paypal_payment set
        # order.status and save on their own success/failure branches
        # (except a couple of PayPal early-return error paths handled by
        # the normalization guard below).
        if processor == "paypal":
            orderId = billingData.get("source_id")
            if not orderId:
                status, response = False, "Missing PayPal order ID"
            else:
                mock_response = ""
                if request and settings.PAYPAL_ENVIRONMENT.lower()[0] != "p":
                    post_data = json.loads(request.body)
                    mock_response = post_data.get("paypalMockResponse")
                status, response = capture_paypal_payment(orderId, order, mock_response)
        elif processor == "square":
            status, response = charge_payment(order, billingData, request)
        else:
            status, response = (
                False,
                {"errors": [{"code": f"Unknown processor: {processor}"}]},
            )

        # Normalize order.status and persist. Payment handlers usually
        # save themselves, but some mocks only mutate the in-memory
        # object, and a few PayPal early-return branches (Missing-id /
        # ApiException / JSON-decode) return without saving at all. We
        # always save so the DB reflects the terminal state, and we coerce
        # PENDING into a concrete terminal before the capacity call so
        # update_capacity_for_status_change doesn't see old==new==PENDING.
        if order.status == Order.PENDING:
            order.status = Order.COMPLETED if status else Order.FAILED
        order.save()

        # Uniform capacity transition PENDING→COMPLETED (confirm) or
        # PENDING→FAILED (release).
        update_capacity_for_status_change(order, Order.PENDING, order.status)

        if status:
            if discount:
                discount.used = discount.used + 1
                discount.save()
            return True, {"errors": []}, order
        return False, response, order

    except Exception as e:
        logger.error(f"Error during checkout: {e}")
        if order.id:
            try:
                order.refresh_from_db()
                if order.status == Order.PENDING:
                    update_capacity_for_status_change(order, Order.PENDING, Order.FAILED)
                    order.status = Order.FAILED
                    order.save()
            except Exception as cleanup_error:
                logger.error(f"Error during checkout cleanup: {cleanup_error}")
        raise


def doZeroCheckout(discount, cartItems, orderItems):
    billingName = ""
    billingEmail = ""
    if cartItems:
        attendee = json.loads(cartItems[0].formData)["attendee"]
        billingName = "{firstName} {lastName}".format(**attendee)
        billingEmail = attendee["email"]
    elif orderItems:
        attendee = orderItems[0].badge.attendee
        billingName = f"{attendee.firstName} {attendee.lastName}"
        billingEmail = attendee.email

    reference = common.get_unique_confirmation_token(Order)

    order = Order(
        total=0,
        reference=reference,
        discount=discount,
        orgDonation=0,
        charityDonation=0,
        status=Order.COMPLETED,
        billingType=Order.COMP,
        billingEmail=billingEmail,
        billingName=billingName,
    )

    price_level_counts = _count_price_levels(cartItems, orderItems)

    try:
        with transaction.atomic():
            levels, error = _check_capacity(price_level_counts)
            if error:
                return False, error, None

            # Directly consume slots — no pending state for zero-cost orders
            for level, count in levels:
                level.consume_slots(count)

            order.save()
            _save_order_items(order, cartItems, orderItems)

            if discount:
                discount.used = discount.used + 1
                discount.save()

        return True, "", order

    except Exception as e:
        logger.error(f"Error during zero-cost checkout: {e}")
        raise


def getCartItemOptionTotal(options):
    optionTotal = 0
    for option in options:
        optionData = PriceLevelOption.objects.get(id=option["id"])
        if optionData.optionExtraType == "int":
            if option["value"]:
                optionTotal += optionData.optionPrice * Decimal(option["value"])
        else:
            optionTotal += optionData.optionPrice
    return optionTotal


def get_order_item_option_total(options):
    optionTotal = 0
    for option in options:
        if option.option.optionExtraType == "int":
            if option.optionValue:
                optionTotal += option.option.optionPrice * Decimal(option.optionValue)
        else:
            optionTotal += option.option.optionPrice
    return optionTotal


def get_discount_total(disc, subtotal):
    """Accept either a ``Discount`` model instance or a string code name.

    Callers in ``cart.py``/``onsite.py``/``onsite_admin.py`` hand in already-
    looked-up ``Discount`` objects, while ``get_line_item_total`` forwards
    the raw session value which is a string. Normalize to a string code
    name before the DB lookup.
    """
    if isinstance(disc, Discount):
        disc = disc.codeName
    try:
        discount = Discount.objects.get(codeName=disc)
    except (Discount.DoesNotExist, ValueError, TypeError):
        return 0
    if discount.isValid():
        if discount.amountOff:
            return discount.amountOff
        elif discount.percentOff:
            return Decimal(float(subtotal) * float(discount.percentOff) / 100)
    return 0


def get_line_item_total(item: Cart | OrderItem, disc: str | None = "") -> Decimal:
    item_total = 0
    discount = 0
    if isinstance(item, Cart):
        post_data = json.loads(item.formData)
        pdp = post_data["priceLevel"]
        price_level = PriceLevel.objects.get(id=pdp["id"])
        item_total = price_level.basePrice

        options = pdp["options"]
        item_total += getCartItemOptionTotal(options)

    elif isinstance(item, OrderItem):
        item_sub_total = item.priceLevel.basePrice
        eff_level = item.badge.effectiveLevel()

        item_total = item_sub_total - eff_level.basePrice if eff_level else item_sub_total

        item_total += get_order_item_option_total(item.attendeeoptions_set.all())

    if disc:
        discount = get_discount_total(disc, item_total)

    return item_total, discount


def get_total(
    cartItems: list[Cart], orderItems: list[OrderItem], disc: str | None = ""
) -> tuple[Decimal, Decimal]:
    total = 0
    total_discount = 0
    if not cartItems and not orderItems:
        return 0, 0

    for item in cartItems:
        item_total, discount = get_line_item_total(item, disc)
        total_discount += discount
        item_total -= discount
        if item_total > 0:
            total += item_total

    for item in orderItems:
        item_total, discount = get_line_item_total(item, disc)
        total_discount += discount
        item_total -= discount
        if item_total > 0:
            total += item_total

    return total, total_discount


def apply_discount(request):
    dis = request.session.get("discount", "")
    if dis:
        return JsonResponse(
            {"success": False, "message": "Only one discount is allowed per order."}
        )

    try:
        postData = json.loads(request.body)
    except ValueError:
        logger.error("Unable to decode JSON for apply_discount()")
        return JsonResponse({"success": False})
    dis = postData["discount"]

    discount = Discount.objects.filter(codeName=dis)
    if discount.count() == 0:
        return JsonResponse({"success": False, "message": "That discount is not valid."})
    discount = discount.first()
    if not discount.isValid():
        return JsonResponse({"success": False, "message": "That discount is not valid."})

    request.session["discount"] = discount.codeName
    return JsonResponse({"success": True})


def add_attendee_to_assistant(request, attendee):
    assistant_id = request.session.get("assistant_id")
    if assistant_id:
        logger.info(f"Add attendee {attendee} to assistant id: {assistant_id}")
        try:
            assistant = DealerAsst.objects.get(pk=assistant_id)
            assistant.attendee = attendee
            assistant.save()
        except DealerAsst.DoesNotExist:
            pass


def create_paypal_order(request: HttpRequest) -> JsonResponse:
    """
    Creates an order in PayPal.

    REST endpoint used by PayPal's frontend script to create an order in
    PayPal's system after the user clicks the pay with PayPal button. Constructs
    cart details to send to PayPal, sends it, and returns the response from the
    PayPal API. The checkout page scripts (the PayPal JavaScript library) use
    the newly created order ID to capture payment after the user verifies their
    cart in PayPal.

    :param request: Incoming HTTP request. May contain donation amounts in the
        body.

    :return: JSON response containing the response body from the PayPal Orders
        API.
    """
    # We don't need to get cart items from the client - they're in the session
    cart_items, order_items, discount_code = get_cart_data_from_session(request)

    # Safety valve (in case session times out before checkout is complete)
    if len(cart_items) == 0 and len(order_items) == 0:
        return common.abort(400, "Session expired or no session is stored for this client")

    try:
        post_data = json.loads(request.body)
    except (ValueError, JSONDecodeError) as e:
        logger.exception(e)
        logger.error("Unable to decode JSON for checkout()")
        return common.abort(400, "Unable to parse input options")

    event = Event.objects.get(default=True)

    discount = Discount.objects.filter(codeName=discount_code)
    discount = discount.first() if discount.count() > 0 and discount.first().isValid() else None

    # Process cart item data and calculate totals
    translated_cart: list[TranslatedCartItem] = []
    for item in cart_items:
        parsed_data = json.loads(item.formData)
        pda = parsed_data["attendee"]
        pdp = parsed_data["priceLevel"]

        # DO NOT SAVE THESE MODELS
        attendee = Attendee(
            preferredName=pda.get("preferredName", ""),
            firstName=pda["firstName"],
            lastName=pda["lastName"],
        )
        priceLevel = PriceLevel.objects.get(id=int(pdp["id"]))

        item_total, discount = get_line_item_total(item, discount_code)
        translated_cart.append(
            {
                "name": f"{event} {priceLevel} - {attendee}",
                "total": item_total,
                "donation": False,
            }
        )

    for item in order_items:
        item_total, discount = get_line_item_total(item, discount_code)
        badge = item.badge
        translated_cart.append(
            {
                "name": str(event) + " " + str(item.priceLevel) + " - " + str(badge.attendee),
                "total": item_total,
                "donation": False,
            }
        )

    subtotal, total_discount = get_total(cart_items, order_items, discount_code)

    porg = Decimal(post_data.get("orgDonation") or "0.00")
    pcharity = Decimal(post_data.get("charityDonation") or "0.00")

    if porg < 0:
        porg = 0
    if pcharity < 0:
        pcharity = 0

    if porg > 0:
        translated_cart.append({"name": f"Donation to {event}", "total": porg, "donation": True})

    if pcharity > 0:
        translated_cart.append(
            {
                "name": f"Donation to {event.charity}",
                "total": pcharity,
                "donation": True,
            }
        )

    total = subtotal + porg + pcharity

    # We only want to set up to capture payment if there is payment due
    if total > 0:
        # Generate (or reuse) the reference token that will become
        # Order.reference. Send it to PayPal as both invoice_id and custom_id
        # so every downstream webhook event carries it back to us.
        reference = request.session.get("pending_paypal_reference")
        if not reference:
            reference = common.get_unique_confirmation_token(Order)
            request.session["pending_paypal_reference"] = reference
        try:
            result = create_unpaid_paypal_order(
                total, total_discount, translated_cart, apis_reference=reference
            )
            return common.success(reason=json.loads(result.text))
        except ApiException as ex:
            return common.abort(ex.response_code, json.loads(ex.response.text))

    return common.abort(400, "Cart total is zero; use the zero-checkout flow")


@idempotency_key(optional=False)
def checkout(request):
    """
    Finalizes checkout, creating order data and capturing payment.
    """

    try:
        post_data: dict = json.loads(request.body)
    except (ValueError, JSONDecodeError) as e:
        logger.exception(e)
        logger.error("Unable to decode JSON for checkout()")
        return common.abort(400, "Unable to parse input options")

    Event.objects.get(default=True)
    session_items = request.session.get("cart_items", [])
    cart_items = list(Cart.objects.filter(id__in=session_items))
    order_items = request.session.get("order_items", [])
    pdisc = request.session.get("discount", "")

    # Safety valve (in case session times out before checkout is complete)
    if len(session_items) == 0 and len(order_items) == 0:
        return common.abort(400, "Session expired or no session is stored for this client")

    discount = Discount.objects.filter(codeName=pdisc)
    discount = discount.first() if discount.count() > 0 and discount.first().isValid() else None

    if order_items:
        order_items = list(OrderItem.objects.filter(id__in=order_items))

    subtotal, _ = get_total(cart_items, order_items, discount)

    if not cart_items and not order_items:
        return common.abort(400, "There is nothing in your cart!")

    porg = Decimal(post_data.get("orgDonation") or "0.00")
    pcharity = Decimal(post_data.get("charityDonation") or "0.00")
    pbill = post_data.get("billingData", {})
    pproc = post_data.get("processor")

    if porg < 0:
        porg = 0
    if pcharity < 0:
        pcharity = 0

    total = subtotal + porg + pcharity

    if subtotal == 0:
        status, message, order = doZeroCheckout(discount, cart_items, order_items)
        if not status:
            return common.abort(400, message)

        existing_order_item = order.orderitem_set.first()
        if existing_order_item:
            add_attendee_to_assistant(request, existing_order_item.badge.attendee)
        common.clear_session(request)
        request.session["last_order_id"] = order.id
        tasks.send_registration_email_task.delay(order.id, order.billingEmail)
        return common.success()

    onsite = post_data.get("onsite", False)
    if onsite:
        reference = common.get_unique_confirmation_token(Order)
        order = Order(
            total=Decimal(total),
            reference=reference,
            discount=discount,
            orgDonation=porg,
            charityDonation=pcharity,
            billingType=Order.UNPAID,
        )
        order.status = "Onsite Pending"
        order.save()

        if cart_items:
            for item in cart_items:
                order_item = cart.saveCart(item)
                order_item.order = order
                order_item.save()

        if discount:
            discount.used = discount.used + 1
            discount.save()

        status = True
        message = "Onsite success"
    else:
        status, message, order = do_checkout(
            pproc,
            pbill,
            total,
            discount,
            cart_items,
            order_items,
            porg,
            pcharity,
            request,
        )

    if status:
        existing_order_item = order.orderitem_set.first()
        if existing_order_item:
            add_attendee_to_assistant(request, existing_order_item.badge.attendee)
        # Delete cart when done
        cart_items = Cart.objects.filter(id__in=session_items)
        cart_items.delete()
        common.clear_session(request)
        request.session["last_order_id"] = order.id
        tasks.send_registration_email_task.delay(order.id, order.billingEmail)

        notify_terminal(request, order)

        return common.success()
    else:
        return common.abort(400, message)


def deleteOrderItem(id):
    orderItems = OrderItem.objects.filter(id=id)
    if orderItems.count() == 0:
        return
    orderItem = orderItems.first()
    orderItem.badge.attendee.delete()
    orderItem.badge.delete()
    orderItem.delete()


def cancel_order(request):
    # (DEPRECATED) XXX [Is it actually? Button still hooked up in frontend -R]
    # remove order from session
    order = request.session.get("order_items", [])
    for item in order:
        deleteOrderItem(item)
    # Delete carts
    sessionItems = request.session.get("cart_items", [])
    cartItems = Cart.objects.filter(id__in=sessionItems)
    cartItems.delete()
    # Clear session values
    common.clear_session(request)
    return common.success()


def notify_terminal(request, order):
    try:
        associated_terminal = request.COOKIES.get("terminal-token")
        if associated_terminal:
            signer = TimestampSigner()
            data_obj = signer.unsign_object(associated_terminal, max_age=60 * 30)
            # We only need one badge ID as onsite will automatically add all
            # badges attached to the order.
            order_item = OrderItem.objects.filter(order_id=order.id).first()
            if order_item:
                mqtt.send_mqtt_message(
                    mqtt.get_topic("web/registration/completed", name=data_obj["terminal"]),
                    {"badgeId": order_item.badge_id},
                )
    except Exception as ex:
        logger.warning(f"Could not use terminal-token: {ex}")
        pass
