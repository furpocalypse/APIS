import json
import logging

from django.forms import model_to_dict
from django.http import (
    HttpResponseBadRequest,
    HttpResponseNotFound,
    HttpResponseServerError,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, render
from paypalserversdk.exceptions.api_exception import ApiException

from registration import tasks
from registration.models import *
from registration.paypal_payments import create_unpaid_paypal_order
from registration.services import CreateAttendeeOptions
from registration.types import TranslatedCartItem

from . import common
from .common import clear_session, getOptionsDict, handler
from .ordering import do_paypal_checkout, doZeroCheckout, get_total

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
    badge_dict = {"id": badge.id}
    level = badge.effectiveLevel()
    existing_order_items = badge.getOrderItems()
    level_dict = {
        "basePrice": level.basePrice,
        "options": getOptionsDict(existing_order_items),
    }
    context = {
        "attendee": attendee,
        "badge": badge,
        "event": event,
        "jsonAttendee": json.dumps(attendee_dict, default=handler),
        "jsonBadge": json.dumps(badge_dict, default=handler),
        "jsonLevel": json.dumps(level_dict, default=handler),
    }
    return render(request, "registration/attendee-upgrade.html", context)


def add_upgrade(request):
    try:
        postData = json.loads(request.body)
    except ValueError:
        logger.error("Unable to decode JSON for add_upgrade()")
        return JsonResponse({"success": False})

    pda = postData["attendee"]
    pdp = postData["priceLevel"]
    pdd = postData["badge"]
    evt = postData["event"]
    Event.objects.get(name=evt)

    if "attendee_id" not in request.session:
        return HttpResponseServerError("Session expired")

    # Update Attendee info
    attendee = Attendee.objects.get(id=pda["id"])
    if not attendee:
        return HttpResponseServerError("Attendee id not found")

    badge = Badge.objects.get(id=pdd["id"])
    priceLevel = PriceLevel.objects.get(id=int(pdp["id"]))

    orderItem = OrderItem(badge=badge, priceLevel=priceLevel, enteredBy="WEB")
    orderItem.save()

    CreateAttendeeOptions(orderItem).save_options(pdp["options"])

    orderItems = request.session.get("order_items", [])
    orderItems.append(orderItem.id)
    request.session["order_items"] = orderItems

    return JsonResponse({"success": True})


def invoice_upgrade(request):
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
            lvl = badge.effectiveLevel()
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


def upgrade_paypal_create(request):
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

    subtotal, total_discount = get_total([], order_items)

    porg = Decimal(post_data.get("orgDonation") or "0.00")
    pcharity = Decimal(post_data.get("charityDonation") or "0.00")
    if porg < 0:
        porg = 0
    if pcharity < 0:
        pcharity = 0

    total = subtotal + porg + pcharity
    if total <= 0:
        return common.abort(400, "Cart total is zero; use the zero-checkout flow")

    event = Event.objects.get(default=True)
    first = order_items[0]
    label = f"{first.priceLevel} - {first.badge.attendee}"
    translated_cart: list[TranslatedCartItem] = [
        {
            "name": f"{event} Upgrade - {label}",
            "total": subtotal - total_discount,
            "donation": False,
        }
    ]
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


def checkout_upgrade(request):
    session_items = request.session.get("order_items", [])
    order_items = list(OrderItem.objects.filter(id__in=session_items))
    if "attendee_id" not in request.session:
        return HttpResponseBadRequest("Session expired")

    attendee = Attendee.objects.get(id=request.session.get("attendee_id"))
    try:
        post_data = json.loads(request.body)
    except ValueError:
        logger.error("Unable to decode JSON for checkout_upgrade()")
        return common.abort(400, "Unable to parse input options")

    subtotal, total_discount = get_total([], order_items)

    if subtotal == 0:
        status, message, order = doZeroCheckout(None, None, order_items)

        if not status:
            return common.abort(400, message)

        return send_upgrade_email(request, attendee, order)

    porg = Decimal(post_data.get("orgDonation") or "0.00")
    pcharity = Decimal(post_data.get("charityDonation") or "0.00")
    if porg < 0:
        porg = 0
    if pcharity < 0:
        pcharity = 0

    total = subtotal + porg + pcharity

    if "orderID" not in post_data:
        return common.abort(400, "Missing PayPal order ID")
    status, message, order = do_paypal_checkout(
        post_data["orderID"],
        total,
        None,
        [],
        order_items,
        porg,
        pcharity,
        request,
        billingData=post_data.get("billingData") or {},
    )

    if status:
        return send_upgrade_email(request, attendee, order)
    else:
        if order is not None:
            order.delete()
        return common.abort(400, message)
