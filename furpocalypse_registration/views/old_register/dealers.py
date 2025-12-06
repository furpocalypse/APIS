import json
import logging
from datetime import datetime
from datetime import timezone as python_tz
from json import JSONDecodeError

from django.forms import model_to_dict
from django.http import (
    HttpResponse,
    HttpResponseNotFound,
    HttpResponseServerError,
    JsonResponse,
)
from django.shortcuts import render
from django.urls import reverse

import furpocalypse_registration.emails
from furpocalypse_registration.models import *

from . import common
from .common import clear_session, handler, logger
# from .ordering import do_checkout, doZeroCheckout, get_discount_total

logger = logging.getLogger(__name__)


def dealers(request, guid):
    event = Event.objects.get(default=True)
    context = {"token": guid, "event": event}
    return render(request, "registration/dealer/dealer-locate.html", context)


def thanks_dealer(request):
    event = Event.objects.get(default=True)
    context = {"event": event}
    return render(request, "registration/dealer/dealer-thanks.html", context)


def done_dealer(request):
    event = Event.objects.get(default=True)
    context = {"event": event}
    return render(request, "registration/dealer/dealer-done.html", context)


def find_dealer_to_add_assistant(request, guid):
    event = Event.objects.get(default=True)
    context = {
        "token": guid,
        "event": event,
        "next": reverse("furpocalypse_registration:find_dealer_to_add_assistant_post"),
    }
    return render(request, "registration/dealer/dealerasst-locate.html", context)


def dealer_asst(request, guid):
    event = Event.objects.get(default=True)
    context = {
        "token": guid,
        "event": event,
        "next": reverse("furpocalypse_registration:find_asst_dealer"),
    }
    return render(request, "registration/dealer/dealerasst-locate.html", context)


def done_asst_dealer(request):
    event = Event.objects.get(default=True)
    context = {"event": event}
    return render(request, "registration/dealer/dealerasst-done.html", context)


def new_dealer(request):
    event = Event.objects.get(default=True)
    venue = event.venue
    tz = timezone.get_current_timezone()
    today = datetime.now(python_tz.utc)
    context = {"event": event, "venue": venue}
    if event.dealerRegStart <= today <= event.dealerRegEnd:
        return render(request, "registration/dealer/dealer-form.html", context)
    elif event.dealerRegStart >= today:
        context["message"] = (
            "is not yet open. Please stay tuned to our social media for updates!"
        )
        return render(request, "registration/dealer/dealer-closed.html", context)
    elif event.dealerRegEnd <= today:
        context["message"] = "has ended."
        return render(request, "registration/dealer/dealer-closed.html", context)


def info_dealer(request):
    event = Event.objects.get(default=True)
    context = {"dealer": None, "event": event}
    try:
        dealerId = request.session["dealer_id"]
    except (ValueError, KeyError):
        return render(request, "registration/dealer/dealer-payment.html", context)

    dealer = Dealer.objects.get(id=dealerId)
    if dealer:
        badge = Badge.objects.filter(
            attendee=dealer.attendee, event=dealer.event
        ).last()
        dealer_dict = model_to_dict(dealer)
        attendee_dict = model_to_dict(dealer.attendee)
        if badge is not None:
            badge_dict = model_to_dict(badge)
        else:
            badge_dict = {}
        table_dict = model_to_dict(dealer.tableSize)

        context = {
            "dealer": dealer,
            "badge": badge,
            "event": dealer.event,
            "jsonDealer": json.dumps(dealer_dict, default=handler),
            "jsonTable": json.dumps(table_dict, default=handler),
            "jsonAttendee": json.dumps(attendee_dict, default=handler),
            "jsonBadge": json.dumps(badge_dict, default=handler),
        }
    return render(request, "registration/dealer/dealer-payment.html", context)


def find_dealer(request):
    post_data = json.loads(request.body)
    email = post_data["email"]
    token = post_data["token"]

    try:
        dealer = Dealer.objects.get(
            attendee__email__iexact=email, registrationToken=token
        )
    except Dealer.DoesNotExist:
        return common.abort(404, "No Dealer Found " + email)

    request.session["dealer_id"] = dealer.id
    return common.success()


def find_dealer_to_add_assistant_post(request):
    post_data = json.loads(request.body)
    email = post_data.get("email")
    token = post_data.get("token")

    if email is None or token is None:
        return common.abort(400, "Email or token missing from form data")

    try:
        dealer = Dealer.objects.get(
            attendee__email__iexact=email, registrationToken=token
        )
    except Dealer.DoesNotExist:
        return common.abort(404, "No dealer found")

    request.session["dealer_id"] = dealer.pk
    return JsonResponse(
        {
            "success": True,
            "message": "DEALER",
            "location": reverse("furpocalypse_registration:add_assistants"),
        }
    )


def find_asst_dealer(request):
    postData = json.loads(request.body)
    email = postData["email"]
    token = postData["token"]

    try:
        dealer_assistant = DealerAsst.objects.get(
            email__iexact=email, registrationToken=token
        )
    except DealerAsst.DoesNotExist:
        return HttpResponseNotFound("No assistant dealer found")

    # Check if they've already registered:
    if dealer_assistant.attendee is not None:
        # Send them to the upgrade path instead
        request.session["attendee_id"] = dealer_assistant.attendee.pk
        request.session["badge_id"] = dealer_assistant.attendee.badge_set.first().pk
        return JsonResponse(
            {
                "success": True,
                "message": "ASSISTANT",
                "location": reverse("furpocalypse_registration:find_upgrade"),
            }
        )

    request.session["assistant_id"] = dealer_assistant.id
    discount = dealer_assistant.event.assistantDiscount
    if discount:
        request.session["discount"] = discount.codeName

    return JsonResponse(
        {
            "success": True,
            "message": "ASSISTANT",
            "location": reverse("furpocalypse_registration:index"),
        }
    )


def invoice_dealer(request):
    """
    Handle dealer invoicing - NOT IMPLEMENTED.
    
    PAYPAL_TODO - Implement dealer invoicing
    This function was disabled during Square removal and needs to be rewritten
    for PayPal integration. Depends on get_dealer_total function which is also
    commented out and requires PayPal order processing to be fully implemented.
    """
    raise NotImplementedError("Dealer invoicing not implemented - requires PayPal integration completion")


def add_assistants(request):
    context = {"attendee": None, "dealer": None}
    try:
        dealerId = request.session["dealer_id"]
    except (KeyError, ValueError):
        return render(request, "registration/dealer/dealerasst-add.html", context)

    dealer = Dealer.objects.get(id=dealerId)
    if dealer.attendee:
        assts = list(DealerAsst.objects.filter(dealer=dealer))
        assistants = []
        for dasst in assts:
            assistants.append(model_to_dict(dasst))
        asst_count_registered = len([ass for ass in assts if ass.attendee is not None])
        context = {
            "attendee": dealer.attendee,
            "dealer": dealer,
            "assistants": assts,
            "extra_assistants_range": range(len(assts), dealer.tableSize.partnerMax),
            "asst_count_registered": asst_count_registered,
            "json_assistants": json.dumps(assistants, default=handler),
        }
    event = Event.objects.get(default=True)
    context["event"] = event
    return render(request, "registration/dealer/dealerasst-add.html", context)


def add_assistants_checkout(request):
    """
    Handle assistant checkout - NOT IMPLEMENTED.
    
    PAYPAL_TODO - Implement assistant checkout
    This function was disabled during Square removal and needs to be rewritten
    for PayPal integration. Depends on do_checkout function from ordering.py
    which requires PayPal order processing to be fully implemented.
    """
    raise NotImplementedError("Assistant checkout not implemented - requires PayPal integration completion")


def add_dealer(request):
    try:
        postData = json.loads(request.body)
    except ValueError as e:
        logger.error("Unable to decode JSON for add_staff()")
        logger.exception(e)
        return JsonResponse({"success": False})

    pda = postData["attendee"]
    pdd = postData["dealer"]
    evt = postData["event"]
    pdp = postData["priceLevel"]
    event = Event.objects.get(name=evt)

    if "dealer_id" not in request.session:
        return HttpResponseServerError("Session expired")

    # Update Dealer info
    try:
        dealer = Dealer.objects.get(id=pdd["id"])
    except dealer.DoesNotExist:
        return HttpResponseServerError("Dealer id not found")

    dealer.businessName = pdd["businessName"]
    dealer.website = pdd["website"]
    dealer.logo = pdd["logo"]
    dealer.description = pdd["description"]
    dealer.license = pdd["license"]
    dealer.needPower = pdd["power"]
    dealer.needWifi = pdd["wifi"]
    dealer.wallSpace = pdd["wall"]
    dealer.nearTo = pdd["near"]
    dealer.farFrom = pdd["far"]
    dealer.reception = pdd["reception"]
    dealer.artShow = pdd["artShow"]
    dealer.charityRaffle = pdd["charityRaffle"]
    dealer.breakfast = pdd["breakfast"]
    dealer.willSwitch = pdd["switch"]
    dealer.asstBreakfast = pdd["asstbreakfast"]
    dealer.event = event

    dealer.save()

    # Update Attendee info
    try:
        attendee = Attendee.objects.get(id=pda["id"])
    except attendee.DoesNotExist:
        return HttpResponseServerError("Attendee id not found")

    attendee.preferredName = pda.get("preferredName", "")
    attendee.firstName = pda["firstName"]
    attendee.lastName = pda["lastName"]
    attendee.address1 = pda["address1"]
    attendee.address2 = pda["address2"]
    attendee.city = pda["city"]
    attendee.state = pda["state"]
    attendee.country = pda["country"]
    attendee.postalCode = pda["postal"]
    attendee.phone = pda["phone"]

    attendee.save()

    badge = Badge.objects.get(
        attendee=attendee,
        event=event,
    )
    badge.badgeName = pda["badgeName"]

    badge.save()

    priceLevel = PriceLevel.objects.get(id=int(pdp["id"]))

    orderItem = OrderItem(badge=badge, priceLevel=priceLevel, enteredBy="WEB")
    orderItem.save()

    for option in pdp["options"]:
        plOption = PriceLevelOption.objects.get(id=int(option["id"]))
        attendeeOption = AttendeeOptions(
            option=plOption, orderItem=orderItem, optionValue=option["value"]
        )
        attendeeOption.save()

    orderItems = request.session.get("order_items", [])
    orderItems.append(orderItem.id)
    request.session["order_items"] = orderItems

    return JsonResponse({"success": True})


def checkout_dealer(request):
    """
    Handle dealer checkout - NOT IMPLEMENTED.
    
    PAYPAL_TODO - Implement dealer checkout
    This function was disabled during Square removal and needs to be rewritten
    for PayPal integration. Depends on get_dealer_total, doZeroCheckout, and
    do_checkout functions which require PayPal order processing to be fully implemented.
    """
    raise NotImplementedError("Dealer checkout not implemented - requires PayPal integration completion")


def addNewDealer(request):
    try:
        postData = json.loads(request.body)
    except ValueError as e:
        logger.warning(f"Unable to decode JSON for addNewDealer(): {e}")
        return common.abort(400, "Unable to decode JSON")

    # create attendee from request post
    pda = postData["attendee"]
    pdd = postData["dealer"]
    evt = postData["event"]

    tz = timezone.get_current_timezone()
    try:
        birthdate = datetime.strptime(
            f'{pda["birthdate"]}:{python_tz.utc}', "%Y-%m-%d:%Z"
        )
    except ValueError as e:
        logger.warning(f"Unable to parse birthdate: {pda['birthdate']} - {e}")
        return common.abort(400, f"Unable to parse birthdate: {pda['birthdate']}")
    event = Event.objects.get(name=evt)

    attendee = Attendee(
        preferredName=pda.get("preferredName", ""),
        firstName=pda["firstName"],
        lastName=pda["lastName"],
        address1=pda["address1"],
        address2=pda["address2"],
        city=pda["city"],
        state=pda["state"],
        country=pda["country"],
        postalCode=pda["postal"],
        phone=pda["phone"],
        email=pda["email"],
        birthdate=birthdate,
        emailsOk=bool(pda["emailsOk"]),
        surveyOk=bool(pda["surveyOk"]),
    )
    attendee.save()

    badge = Badge(
        attendee=attendee,
        event=event,
        badgeName=pda["badgeName"],
        signature_svg=pda.get("signature_svg"),
        signature_bitmap=pda.get("signature_bitmap"),
    )
    badge.save()

    tablesize = TableSize.objects.get(id=pdd["tableSize"])
    dealer = Dealer(
        attendee=attendee,
        event=event,
        businessName=pdd["businessName"],
        logo=pdd["logo"],
        website=pdd["website"],
        description=pdd["description"],
        license=pdd["license"],
        needPower=pdd["power"],
        needWifi=pdd["wifi"],
        wallSpace=pdd["wall"],
        nearTo=pdd["near"],
        farFrom=pdd["far"],
        tableSize=tablesize,
        chairs=pdd["chairs"],
        reception=pdd["reception"],
        artShow=pdd["artShow"],
        charityRaffle=pdd["charityRaffle"],
        breakfast=pdd["breakfast"],
        willSwitch=pdd["switch"],
        tables=pdd["tables"],
        agreeToRules=pdd["agreeToRules"],
        asstBreakfast=pdd["asstbreakfast"],
    )
    dealer.save()

    partners = pdd["partners"]
    for partner in partners:
        dealerPartner = DealerAsst(
            dealer=dealer,
            event=event,
            name=partner["name"],
            email=partner["email"],
            license=partner["license"],
        )
        dealerPartner.save()

    try:
        furpocalypse_registration.emails.send_dealer_application_email(dealer.id)
    except Exception as e:
        logger.error("Error sending DealerApplicationEmail.")
        logger.exception(e)
        dealerEmail = get_dealer_email()
        return JsonResponse(
            {
                "success": False,
                "message": "Your registration succeeded but we may have been unable to send you a confirmation email. If you have any questions, please contact {0}.".format(
                    dealerEmail
                ),
            }
        )
    return JsonResponse({"success": True})


def getTableSizes(request):
    event = Event.objects.get(default=True)
    sizes = TableSize.objects.filter(event=event)
    data = [
        {
            "name": size.name,
            "id": size.id,
            "description": size.description,
            "chairMin": size.chairMin,
            "chairMax": size.chairMax,
            "tableMin": size.tableMin,
            "tableMax": size.tableMax,
            "partnerMin": size.partnerMin,
            "partnerMax": size.partnerMax,
            "basePrice": str(size.basePrice),
        }
        for size in sizes
    ]
    return HttpResponse(json.dumps(data), content_type="application/json")


def get_dealer_email(event=None):
    """
    Retrieves the email address to show on error messages in the dealer
    registration form for a specified event.  If no event specified, uses
    the first default event.  If no email is listed there, returns the
    default of APIS_DEFAULT_EMAIL in settings.py.
    """
    if event is None:
        try:
            event = Event.objects.get(default=True)
        except BaseException:
            return settings.APIS_DEFAULT_EMAIL
    if event.dealerEmail == "":
        return settings.APIS_DEFAULT_EMAIL
    return event.dealerEmail


def get_dealer_total(orderItems, discount, dealer):
    """
    Calculate dealer total - NOT IMPLEMENTED.
    
    PAYPAL_TODO - Implement dealer total calculation
    This function was disabled during Square removal and needs to be rewritten
    for PayPal integration. Depends on get_discount_total function from ordering.py
    which requires PayPal order processing to be fully implemented.
    """
    raise NotImplementedError("Dealer total calculation not implemented - requires PayPal integration completion")
