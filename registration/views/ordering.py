import json
import logging
import time
from json import JSONDecodeError
from typing import Any

from apimatic_core.response_handler import ApiResponse
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.http.response import HttpResponse
from idempotency_key.decorators import idempotency_key
from paypalserversdk.api_helper import ApiHelper
from paypalserversdk.controllers.orders_controller import OrdersController
from paypalserversdk.controllers.payments_controller import PaymentsController
from paypalserversdk.exceptions.error_exception import ErrorException
from paypalserversdk.http.auth.o_auth_2 import ClientCredentialsAuthCredentials
from paypalserversdk.logging.configuration.api_logging_configuration import (
    LoggingConfiguration,
    RequestLoggingConfiguration,
    ResponseLoggingConfiguration,
)
from paypalserversdk.models.amount_breakdown import AmountBreakdown
from paypalserversdk.models.amount_with_breakdown import AmountWithBreakdown
from paypalserversdk.models.capture_request import CaptureRequest
from paypalserversdk.models.card_attributes import CardAttributes
from paypalserversdk.models.card_request import CardRequest
from paypalserversdk.models.card_verification import CardVerification
from paypalserversdk.models.checkout_payment_intent import CheckoutPaymentIntent
from paypalserversdk.models.item import Item
from paypalserversdk.models.item_category import ItemCategory
from paypalserversdk.models.money import Money
from paypalserversdk.models.order_application_context_shipping_preference import (
    ShippingPreference,
)
from paypalserversdk.models.order_request import OrderRequest
from paypalserversdk.models.orders_card_verification_method import (
    OrdersCardVerificationMethod,
)
from paypalserversdk.models.payment_source import PaymentSource
from paypalserversdk.models.paypal_experience_landing_page import (
    PaypalExperienceLandingPage,
)
from paypalserversdk.models.paypal_experience_user_action import (
    PaypalExperienceUserAction,
)
from paypalserversdk.models.paypal_wallet import PaypalWallet
from paypalserversdk.models.paypal_wallet_experience_context import (
    PaypalWalletExperienceContext,
)
from paypalserversdk.models.purchase_unit_request import PurchaseUnitRequest
from paypalserversdk.models.shipping_details import ShippingDetails
from paypalserversdk.models.shipping_option import ShippingOption
from paypalserversdk.models.shipping_type import ShippingType
from paypalserversdk.paypal_serversdk_client import PaypalServersdkClient

import registration.emails
from registration.models import *
from registration.payments import charge_square_payment

from . import cart, common

logger = logging.getLogger(__name__)


paypal_client: PaypalServersdkClient = PaypalServersdkClient(
    client_credentials_auth_credentials=ClientCredentialsAuthCredentials(
        o_auth_client_id=settings.PAYPAL_CLIENT_ID,
        o_auth_client_secret=settings.PAYPAL_CLIENT_SECRET,
    ),
    logging_configuration=LoggingConfiguration(
        log_level=logging.INFO,
        # Disable masking of sensitive headers for Sandbox testing.
        # This should be set to True (the default if unset)in production.
        mask_sensitive_headers=False,
        request_logging_config=RequestLoggingConfiguration(
            log_headers=True, log_body=True
        ),
        response_logging_config=ResponseLoggingConfiguration(
            log_headers=True, log_body=True
        ),
    ),
)


orders_controller: OrdersController = paypal_client.orders
payments_controller: PaymentsController = paypal_client.payments


def do_checkout(
    billingData,
    total,
    discount,
    cartItems,
    orderItems,
    donationOrg,
    donationCharity,
    request=None,
):
    event = Event.objects.get(default=True)
    reference = common.get_unique_confirmation_token(Order)

    order = Order(
        total=Decimal(total),
        reference=reference,
        discount=discount,
        orgDonation=donationOrg,
        charityDonation=donationCharity,
    )

    # Address collection is marked as required by event
    if event.collectBillingAddress:
        try:
            order.billingName = "{0} {1}".format(
                billingData["cc_firstname"], billingData["cc_lastname"]
            )
            order.billingAddress1 = billingData["address1"]
            order.billingAddress2 = billingData["address2"]
            order.billingCity = billingData["city"]
            order.billingState = billingData["state"]
            order.billingCountry = billingData["country"]
            order.billingEmail = billingData["email"]
            order.billingPostal = billingData["postal"]
        except KeyError as e:
            common.abort(
                400,
                "Address collection is required, but request is missing required field: {0}".format(
                    e
                ),
            )

    status, response = charge_square_payment(order, billingData, request)

    if status:
        order.save()

        if cartItems:
            for item in cartItems:
                order_item = cart.saveCart(item)
                order_item.order = order
                order_item.save()
        elif orderItems:
            for order_item in orderItems:
                order_item.order = order
                order_item.save()

        if discount:
            discount.used = discount.used + 1
            discount.save()
        return True, "", order

    return False, response, order


def doZeroCheckout(discount, cartItems, orderItems):
    billingName = ""
    billingEmail = ""
    if cartItems:
        attendee = json.loads(cartItems[0].formData)["attendee"]
        billingName = "{firstName} {lastName}".format(**attendee)
        billingEmail = attendee["email"]
    elif orderItems:
        attendee = orderItems[0].badge.attendee
        billingName = "{0} {1}".format(attendee.firstName, attendee.lastName)
        billingEmail = attendee.email

    reference = common.get_unique_confirmation_token(Order)

    order = Order(
        total=0,
        reference=reference,
        discount=discount,
        orgDonation=0,
        charityDonation=0,
        status="Complete",
        billingType=Order.COMP,
        billingEmail=billingEmail,
        billingName=billingName,
    )
    order.save()

    if cartItems:
        for item in cartItems:
            orderItem = cart.saveCart(item)
            orderItem.order = order
            orderItem.save()
    elif orderItems:
        for oitem in orderItems:
            oitem.order = order
            oitem.save()

    if discount:
        discount.used = discount.used + 1
        discount.save()
    return True, "", order


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
    try:
        discount = Discount.objects.get(codeName=disc)
    except Discount.DoesNotExist:
        return 0
    if discount.isValid():
        if discount.amountOff:
            return discount.amountOff
        elif discount.percentOff:
            return Decimal(float(subtotal) * float(discount.percentOff) / 100)
    return 0


def get_total(cartItems, orderItems, disc=""):
    total = 0
    total_discount = 0
    if not cartItems and not orderItems:
        return 0, 0

    for item in cartItems:
        post_data = json.loads(item.formData)
        pdp = post_data["priceLevel"]
        price_level = PriceLevel.objects.get(id=pdp["id"])
        item_total = price_level.basePrice

        options = pdp["options"]
        item_total += getCartItemOptionTotal(options)

        if disc:
            discount = get_discount_total(disc, item_total)
            total_discount += discount
            item_total -= discount

        if item_total > 0:
            total += item_total

    for item in orderItems:
        item_sub_total = item.priceLevel.basePrice
        eff_level = item.badge.effectiveLevel()

        if eff_level:
            item_total = item_sub_total - eff_level.basePrice
        else:
            item_total = item_sub_total

        item_total += get_order_item_option_total(item.attendeeoptions_set.all())

        if disc:
            discount = get_discount_total(disc, item_total)
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
    except ValueError as e:
        logger.error("Unable to decode JSON for apply_discount()")
        return JsonResponse({"success": False})
    dis = postData["discount"]

    discount = Discount.objects.filter(codeName=dis)
    if discount.count() == 0:
        return JsonResponse(
            {"success": False, "message": "That discount is not valid."}
        )
    discount = discount.first()
    if not discount.isValid():
        return JsonResponse(
            {"success": False, "message": "That discount is not valid."}
        )

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


# TODO
def validate_cart(cart: Dict[str, Any]) -> Dict[str, Any]:
    """
    Check that the JSON we get matches against what is possible
    to order.  Since what we get client side is unsanitized, an attack vector
    would be to create custom-crafted carts which could be used to transfer
    funds from us to the user or provide free everything.
    """
    return cart


def register_unpaid_order():
    raise RuntimeError("Use the actual function call!")


# TODO
def create_order(request: HttpRequest) -> JsonResponse:
    """
    In the PayPal order flow, this is the second endpoint.

    REST endpoint used by PayPal's frontend script to create an order after the
    user enters their information into PayPal.  The object is presumably
    created by us, but could have been maliciously altered, so we must
    validate it. Then with the valid order, we construct the call details
    which get sent to PayPal, WE send those details (so it is secure between
    us and PayPal), then return an ID to the user. The user then uses that
    (via the PayPal JavaScript library) to make their payment calls, and call
    the final step of the ordering process in `capture_order()`.

    :param request: Incoming HTTP request.  The 'cart' item is defined
        somewhere (probably the webpage) that gets redirected back to us
        indicating what they ordered.  This object needs validation.

    :return: JSON response containing the response body from the PayPal Orders
        API.
    """
    # Taking a stab at what this might look like.
    try:
        request_body = json.loads(request.body)
    except ValueError as e:
        logger.error("Unable to decode JSON for create_order()")
        return JsonResponse({"success": False})

    order_items = validate_cart(request_body)
    register_unpaid_order(
        order_items
    )  # TODO: find the existing function which does this, update it, and use it.

    # use the cart information passed from the front-end to calculate the order amount detals
    order = orders_controller.create_order(
        {
            "body": OrderRequest(
                intent=CheckoutPaymentIntent.CAPTURE,
                purchase_units=[
                    PurchaseUnitRequest(
                        amount=AmountWithBreakdown(
                            currency_code="USD",
                            value="100",
                            breakdown=AmountBreakdown(
                                item_total=Money(currency_code="USD", value="100")
                            ),
                        ),
                        items=[
                            Item(
                                name=item.itemName,
                                unit_amount=Money(
                                    currency_code="USD", value=str(item.individualPrice)
                                ),
                                quantity=str(item.quantity),
                                description=item.description,
                                sku=item.pk,
                                category=ItemCategory.DIGITAL_GOODS,
                            )
                            for item in request_body["cart"]
                        ],
                    )
                ],
            )
        }
    )

    return HttpResponse(
        request_body=json.dumps(order.body), status=200, mimetype="application/json"
    )


# TODO
@idempotency_key(optional=False)
def checkout(request: HttpRequest) -> HttpResponse:
    """
    In the PayPal order flow, this is the first endpoint.  It returns the
    webpage with PayPal's JavaSceript API.

    This mainly is talkback between the merchant server (us) and the person
    checking out.  The results get sent back to us from an object we (if not
    maliciously altered) create.  See `create_order()` for the next step.
    """

    event = Event.objects.get(default=True)
    session_items = request.session.get("cart_items", [])
    cart_items = list(Cart.objects.filter(id__in=session_items))
    order_items = request.session.get("order_items", [])
    pdisc = request.session.get("discount", "")

    # Safety valve (in case session times out before checkout is complete)
    if len(session_items) == 0 and len(order_items) == 0:
        common.abort(400, "Session expired or no session is stored for this client")

    try:
        post_data = json.loads(request.body)
    except (ValueError, JSONDecodeError) as e:
        logger.exception(e)
        logger.error("Unable to decode JSON for checkout()")
        return common.abort(400, "Unable to parse input options")

    discount = Discount.objects.filter(codeName=pdisc)
    if discount.count() > 0 and discount.first().isValid():
        discount = discount.first()
    else:
        discount = None

    if order_items:
        order_items = list(OrderItem.objects.filter(id__in=order_items))

    subtotal, _ = get_total(cart_items, order_items, discount)

    if not cart_items and not order_items:
        return common.abort(400, "There is nothing in your cart!")

    if subtotal == 0:
        status, message, order = doZeroCheckout(discount, cart_items, order_items)
        if not status:
            return common.abort(400, message)

        existing_order_item = order.orderitem_set.first()
        if existing_order_item:
            add_attendee_to_assistant(request, existing_order_item.badge.attendee)
        common.clear_session(request)
        try:
            registration.emails.send_registration_email(order, order.billingEmail)
        except Exception as e:
            logger.error("Error sending RegistrationEmail - zero sum.")
            logger.exception(e)
            registration_email = common.get_registration_email(event)
            return common.abort(
                400,
                "Your payment succeeded but we may have been unable to send you a confirmation email. If you do not "
                "receive one within the next hour, please contact {0} to get your confirmation number.".format(
                    registration_email
                ),
            )
        return common.success()

    porg = Decimal(post_data.get("orgDonation") or "0.00")
    pcharity = Decimal(post_data.get("charityDonation") or "0.00")
    pbill = post_data["billingData"]

    if porg < 0:
        porg = 0
    if pcharity < 0:
        pcharity = 0

    total = subtotal + porg + pcharity

    onsite = post_data["onsite"]
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
            pbill, total, discount, cart_items, order_items, porg, pcharity, request
        )

    if status:
        existing_order_item = order.orderitem_set.first()
        if existing_order_item:
            add_attendee_to_assistant(request, existing_order_item.badge.attendee)
        # Delete cart when done
        cart_items = Cart.objects.filter(id__in=session_items)
        cart_items.delete()
        common.clear_session(request)
        try:
            registration.emails.send_registration_email(order, order.billingEmail)
        except Exception as e:
            event = Event.objects.get(default=True)
            registration_email = common.get_registration_email(event)

            logger.error("Error sending RegistrationEmail.")
            logger.exception(e)
            return common.abort(
                500,
                "Your payment succeeded but we may have been unable to send you a confirmation email. If you do not "
                "receive one within the next hour, please contact {0} to get your confirmation number.".format(
                    registration_email
                ),
            )

        return common.success()
    else:
        return common.abort(400, message)


# TODO
def capture_order(request: HttpRequest) -> HttpResponse:
    """
    This is the third and final endpoint for the merchant server to implement
    in order to support the PayPal order flow.  We get back from the user a
    payment validation token.  We take this token and send it to PayPal to
    validate the payment. We take that response, handle whatever happened with
    that payment, and register the transaction accordingly.
    """

    raise RuntimeError("Not implemented.")

    order: ApiResponse = orders_controller.capture_order(
        {"id": json.loads(request.body), "prefer": "return=representation"}
    )
    return HttpResponse(
        request_body=json.dumps(order.body), status=200, mimetype="application/json"
    )


def deleteOrderItem(id):
    orderItems = OrderItem.objects.filter(id=id)
    if orderItems.count() == 0:
        return
    orderItem = orderItems.first()
    orderItem.badge.attendee.delete()
    orderItem.badge.delete()
    orderItem.delete()


# TODO
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
