import json
import logging
from typing import Any, Literal

from django.forms import model_to_dict
from django.http import (
    HttpRequest,
    HttpResponseBadRequest,
    HttpResponseNotFound,
    HttpResponseServerError,
    JsonResponse,
)
from django.http.response import HttpResponse
from django.shortcuts import get_object_or_404, render
from paypalserversdk.exceptions.api_exception import ApiException

from registration import tasks
from registration.models import Attendee, Badge, Decimal, Event, Order, OrderItem, PriceLevel
from registration.paypal_payments import create_unpaid_paypal_order
from registration.services import CreateAttendeeOptions
from registration.types import TranslatedCartItem

from . import common
from .common import clear_session, getOptionsDict, to_json_safe
from .ordering import do_checkout, doZeroCheckout, get_total

logger = logging.getLogger(__name__)


def upgrade(request, guid):
    event = Event.objects.get(default=True)
    context = {"token": guid, "event": event}
    return render(request, "registration/attendee-locate.html", context)


def info_upgrade(request):
    try:
        postData = json.loads(request.body)
    except ValueError:
        logger.error("Unable to decode JSON for info_upgrade()")
        return JsonResponse({"success": False}, status=400)

    email = postData.get("email")
    token = postData.get("token")
    if email is None or token is None:
        return HttpResponseBadRequest("email, token are required fields")

    badge = get_object_or_404(Badge, registrationToken=token)

    attendee = badge.attendee
    if attendee.email.lower() != email.lower():
        return HttpResponseNotFound("No Record Found")

    request.session["attendee_id"] = attendee.id
    request.session["badge_id"] = badge.id
    return JsonResponse({"success": True, "message": "ATTENDEE"})


def find_upgrade(request):
    event = Event.objects.get(default=True)
    context = {"attendee": None, "event": event}
    try:
        attendee_id = request.session["attendee_id"]
        badge_id = request.session["badge_id"]
    except KeyError:
        return render(request, "registration/attendee-upgrade.html", context, status=400)

    attendee = get_object_or_404(Attendee, id=attendee_id)
    badge = get_object_or_404(Badge, id=badge_id)
    attendee_dict = model_to_dict(attendee)
    badge_dict = {"id": badge.pk}
    level = badge.effectiveLevel()
    if level is None:
        raise
    existing_order_items = badge.getOrderItems()
    level_dict = {
        "basePrice": level.basePrice if isinstance(level, PriceLevel) else level,
        "options": getOptionsDict(existing_order_items),
    }
    context = {
        "attendee": attendee,
        "badge": badge,
        "event": event,
        # Plain dicts for `{% json_script %}`; closes JS-string-XSS vector.
        "jsonAttendee": to_json_safe(attendee_dict),
        "jsonBadge": to_json_safe(badge_dict),
        "jsonLevel": to_json_safe(level_dict),
    }
    return render(request, "registration/attendee-upgrade.html", context)


def add_upgrade(request):
    try:
        postData = json.loads(request.body)
    except ValueError:
        logger.error("Unable to decode JSON for add_upgrade()")
        return JsonResponse({"success": False})

    pdp = postData["priceLevel"]
    pdd = postData["badge"]
    evt = postData["event"]
    event = Event.objects.get(name=evt)

    # Audit P1.8 (BOLA / IDOR fix): the attendee and badge being modified
    # must be the ones bound to *this* session, NOT whatever the request
    # body says. Reading attendee/badge ids from the body let an
    # authenticated attendee mutate someone else's records.
    attendee_id = request.session.get("attendee_id")
    if attendee_id is None:
        return HttpResponseServerError("Session expired")

    try:
        attendee = Attendee.objects.get(id=attendee_id)
    except Attendee.DoesNotExist:
        return HttpResponseServerError("Attendee id not found")

    # The badge id IS supplied by the body (an attendee can have multiple
    # badges across events), but it MUST belong to the session attendee
    # AND to the requested event. Reject any badge that doesn't.
    try:
        badge = Badge.objects.get(id=pdd["id"], attendee=attendee, event=event)
    except Badge.DoesNotExist:
        logger.warning(
            "BOLA attempt in add_upgrade: session attendee %s tried to "
            "modify badge %s for event %s",
            attendee_id,
            pdd.get("id"),
            event.pk,
        )
        return HttpResponseServerError("Badge not found")

    priceLevel = PriceLevel.objects.get(id=int(pdp["id"]))

    orderItem = OrderItem(badge=badge, priceLevel=priceLevel, enteredBy="WEB")
    orderItem.save()

    CreateAttendeeOptions(orderItem).save_options(pdp["options"])

    orderItems = request.session.get("order_items", [])
    orderItems.append(orderItem.pk)
    request.session["order_items"] = orderItems

    return JsonResponse({"success": True})


def invoice_upgrade(request: HttpRequest) -> HttpResponse:
    sessionItems = request.session.get("order_items", [])
    if not sessionItems:
        context = {"orderItems": [], "total": 0, "discount": {}}
        clear_session(request)
    else:
        attendeeId = request.session.get("attendee_id", -1)
        badgeId = request.session.get("badge_id", -1)
        if attendeeId == -1 or badgeId == -1:
            context = {"orderItems": [], "total": 0, "discount": {}}
            clear_session(request)
        else:
            badge = Badge.objects.get(id=badgeId)
            attendee = Attendee.objects.get(id=attendeeId)
            lvl: PriceLevel | Literal["Unpaid"] | None = badge.effectiveLevel()
            if not isinstance(lvl, PriceLevel):
                return common.abort(400, "Must upgrade an existing badge.")
            lvl_dict = {"basePrice": lvl.basePrice}
            orderItems = list(OrderItem.objects.filter(id__in=sessionItems))
            total, total_discount = get_total([], orderItems)
            context = {
                "orderItems": orderItems,
                "total": total,
                "total_discount": total_discount,
                "attendee": attendee,
                "prevLevel": lvl_dict,
                "event": badge.event,
            }
    return render(request, "registration/upgrade-checkout.html", context)


def done_upgrade(request):
    event = Event.objects.get(default=True)
    order = None
    last_order_id = request.session.get("last_order_id")
    if last_order_id:
        order = Order.objects.filter(id=last_order_id).first()
    context = {"event": event, "order": order}
    return render(request, "registration/upgrade-done.html", context)


def send_upgrade_email(request, attendee, order):
    clear_session(request)
    request.session["last_order_id"] = order.id
    tasks.send_upgrade_payment_email_task.delay(attendee.id, order.id)
    return JsonResponse({"success": True})


def upgrade_paypal_create(request: HttpRequest) -> JsonResponse:
    """Create a PayPal order for an upgrade checkout.

    Mirrors :func:`registration.views.ordering.create_paypal_order` but
    uses the pre-staged OrderItems from session (populated by
    :func:`add_upgrade`).
    """
    session_items = request.session.get("order_items", [])
    order_items = list(OrderItem.objects.filter(id__in=session_items))
    if "attendee_id" not in request.session:
        return common.abort(400, "Session expired")
    if not order_items:
        return common.abort(400, "No upgrade items in session")

    try:
        post_data = json.loads(request.body)
    except ValueError:
        logger.error("Unable to decode JSON for upgrade_paypal_create()")
        return common.abort(400, "Unable to parse input options")

    _subtotal, _total_discount = get_total([], order_items)
    subtotal: Decimal = Decimal(_subtotal)
    total_discount: Decimal = Decimal(_total_discount)

    porg: Decimal = max(Decimal(post_data.get("orgDonation") or "0.00"), Decimal("0.00"))
    pcharity: Decimal = max(Decimal(post_data.get("charityDonation") or "0.00"), Decimal("0.00"))

    total: Decimal = subtotal + porg + pcharity
    if total <= 0:
        return common.abort(400, "Cart total is zero; use the zero-checkout flow")

    event = Event.objects.get(default=True)
    first: OrderItem = order_items[0]
    badge: Badge | None = first.badge
    if not isinstance(badge, Badge):
        raise
    label = f"{first.priceLevel} - {badge.attendee}"
    translated_cart: list[TranslatedCartItem] = [
        TranslatedCartItem(
            name=f"{event} Upgrade - {label}",
            total=subtotal - total_discount,
            donation=False,
        )
    ]
    if porg > 0:
        translated_cart.append(
            TranslatedCartItem(name=f"Donation to {event}", total=porg, donation=True)
        )
    if pcharity > 0:
        translated_cart.append(
            TranslatedCartItem(
                name=f"Donation to {event.charity}",
                total=pcharity,
                donation=True,
            )
        )

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


def checkout_upgrade(request: HttpRequest) -> HttpResponse:
    status: bool
    message: str | dict[str, Any] | None = ""
    session_items = request.session.get("order_items", [])
    order_items: list[OrderItem] = [
        i for i in OrderItem.objects.filter(id__in=session_items) if isinstance(i, OrderItem)
    ]
    if "attendee_id" not in request.session:
        return HttpResponseBadRequest("Session expired")

    key = request.session.get("attendee_id")
    if not isinstance(key, int):
        raise
    attendee: Attendee = Attendee.objects.get(id=key)
    try:
        post_data = json.loads(request.body)
    except ValueError:
        logger.error("Unable to decode JSON for checkout_upgrade()")
        return common.abort(400, "Unable to parse input options")

    subtotal, _total_discount = get_total([], order_items)

    if subtotal == 0:
        status, message, order = doZeroCheckout(None, [], order_items)

        if not status:
            return common.abort(400, message if message else "Missing abort message")

        return send_upgrade_email(request, attendee, order)

    porg = max(Decimal(post_data.get("orgDonation") or "0.00"), Decimal("0.00"))
    pcharity = max(Decimal(post_data.get("charityDonation") or "0.00"), Decimal("0.00"))

    total = subtotal + porg + pcharity

    pproc = post_data.get("processor")
    pbill = post_data.get("billingData", {})
    if pproc == "paypal" and "source_id" not in pbill:
        return common.abort(400, "Missing PayPal order ID")

    status, message, order = do_checkout(
        processor=pproc,
        billingData=pbill,
        total=total,
        discount=None,
        cartItems=[],
        orderItems=order_items,
        donationOrg=porg,
        donationCharity=pcharity,
    )

    if status:
        return send_upgrade_email(request, attendee, order)
    return common.abort(400, message if isinstance(message, str) else "Missing error message.")
