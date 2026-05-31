import base64
import json
import logging
import re
import secrets
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.contrib.messages import get_messages
from django.contrib.postgres.search import TrigramSimilarity
from django.db import transaction
from django.db.models import Case, F, Func, Q, Sum, Value, When
from django.http import HttpRequest, HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe

from registration import admin, mqtt, payments
from registration.models import (
    AttendeeOptions,
    Badge,
    Cashdrawer,
    Department,
    Discount,
    Event,
    Firebase,
    Order,
    OrderItem,
    PrintHistory,
    ShirtSizes,
    Staff,
    generate_discount_code,
    get_random_token,
)
from registration.payments_sanitize import sanitize_api_data
from registration.signing import (
    mint_terminal_token,
    print_capability_signer,
    resolve_terminal_token,
)
from registration.views.attendee import get_attendee_age
from registration.views.ordering import (
    get_discount_total,
    get_order_item_option_total,
)

logger = logging.getLogger(__name__)

TWOPLACES = Decimal(10) ** -2


def get_active_terminal(request) -> Firebase | None:
    term_id = request.session.get("terminal")
    if term_id:
        try:
            return Firebase.objects.get(pk=int(term_id))
        except Firebase.DoesNotExist:
            return None
    return None


# MED-2 (OWASP A07 / ASVS V3.2.1): binding a terminal to a staff session
# from a bare ``?terminal=<id>`` param trusted the URL indefinitely — a
# session's active-terminal context could be flipped by anyone who could
# reach the staff-gated endpoint. Re-validate possession of the terminal
# by requiring the device's signed ``terminal-token`` cookie (issued
# HttpOnly by ``regtoken`` via the salted, id+epoch-bound
# registration.signing context — 30 min window, the same context
# notify_terminal verifies) to match the terminal. Bearer-token paths
# (complete_square_transaction) set the session terminal only after
# Firebase.find_by_token and never go through these URL-param binders, so
# they are unaffected.
_TERMINAL_TOKEN_MAX_AGE = 60 * 30
_security_logger = logging.getLogger("fm_eventmanager.security")


def _request_proves_terminal(request, terminal: Firebase) -> bool:
    """True iff the request carries a valid signed terminal-token cookie
    bound (by id + rotation epoch, via the salted terminal-token context)
    to ``terminal``. Peer-review ATTACK-1/2: salt-namespaced so a
    print-capability blob cannot be replayed here, and bound to the
    immutable id (not the mutable name) with rotation invalidation."""
    proven = resolve_terminal_token(request.COOKIES.get("terminal-token"), _TERMINAL_TOKEN_MAX_AGE)
    return proven is not None and proven.id == terminal.id


def _audit_cash_action(
    request, action, *, amount=None, terminal=None, outcome="success", reference=None
):
    """LOW-4 (S33-j / ASVS V7.1 / Logging cheat sheet): who/what/when/
    where/outcome for every money-handling admin action.

    Emitted on the dedicated ``fm_eventmanager.security`` logger (S35:
    own handler, ``propagate=False``, pinned WARNING) so it survives a
    production root-level tightening and ships to centralized,
    append-only aggregation — the tamper-evident sink. Carries the
    actor identity by design (accountability); no attendee PII.
    """
    _security_logger.warning(
        "cash-drawer audit: user=%s action=%s amount=%s terminal=%s reference=%s outcome=%s",
        getattr(request.user, "username", "?"),
        action,
        "-" if amount is None else amount,
        terminal.name if terminal is not None else "unbound",
        reference or "-",
        outcome,
    )


@require_safe
@staff_member_required
def onsite_admin(request):
    # Modify a dummy session variable to keep it alive
    request.session["heartbeat"] = time.time()

    get_terminal_from_request(request)

    return render(request, "registration/spa-host.html")


@require_safe
@staff_member_required
def onsite_admin_terminals(request):
    terminals = list(Firebase.objects.order_by("name").all())

    data = []
    for terminal in terminals:
        data.append(
            {
                "id": terminal.id,
                "name": terminal.name,
                "cashdrawer": terminal.cashdrawer,
                "printViaMqtt": (terminal.print_via_mqtt.name if terminal.print_via_mqtt else None),
                "paymentType": terminal.payment_type,
                "backgroundColor": terminal.background_color,
                "foregroundColor": terminal.foreground_color,
                "squareTerminal": terminal.square_terminal_id is not None,
            }
        )

    return JsonResponse({"terminals": data})


@require_safe
@staff_member_required
def onsite_admin_context(request):
    terminals = list(Firebase.objects.order_by("name").all())

    selected_terminal = None
    mqtt_context = None

    terminal_id = request.GET.get("terminal", None)
    if terminal_id:
        terminal = Firebase.objects.get(id=terminal_id)
        # MED-2: only bind the terminal to this session if the request
        # proves possession of that terminal (signed terminal-token
        # cookie). Otherwise skip the bind and render with no selected
        # terminal — the page still works, the URL param alone cannot
        # flip a staff session's active-terminal context.
        if _request_proves_terminal(request, terminal):
            request.session["terminal"] = terminal.id

            selected_terminal = {
                "id": terminal.id,
                "features": {
                    "card": terminal.payment_type is not None,
                    "cashdrawer": terminal.cashdrawer,
                    "prompt": terminal.payment_type == Firebase.MQTT_REGISTER_APP,
                    "squareTerminal": terminal.square_terminal_id is not None,
                },
            }

            mqtt_context = {
                "broker": getattr(settings, "MQTT_EXTERNAL_BROKER", None),
                "auth": mqtt.get_onsite_admin_token(terminal),
            }
        else:
            _security_logger.warning(
                "terminal bind rejected: no valid terminal-token proof (onsite_admin_context)"
            )

    context = {
        "user": {
            "id": request.user.id,
            "email": request.user.email,
        },
        "mqtt": mqtt_context,
        "shirtSizes": [{"name": s.name, "id": s.id} for s in ShirtSizes.objects.all()],
        "departments": sorted(dept.name for dept in Department.objects.all()),
        "permissions": {
            "cash": request.user.has_perm("order.cash"),
            "cashAdmin": request.user.has_perm("order.cash_admin"),
            "discount": request.user.has_perm("order.discount"),
        },
        "terminals": {
            "selected": selected_terminal,
            "available": [{"id": terminal.id, "name": terminal.name} for terminal in terminals],
        },
        "messages": get_messages_list(request),
    }

    return JsonResponse(context)


@dataclass
class SearchFields:
    query: str
    birthday: str | None = None
    badge_ids: list[int] | None = None

    @classmethod
    def parse(cls, query: str) -> "SearchFields":
        badge_nums = re.search(r"num:([0-9,]+)", query)
        if badge_nums:
            try:
                badge_ids = [int(num) for num in badge_nums.group(1).split(",")]
                return SearchFields(badge_ids=badge_ids, query="")
            except ValueError:
                query = query.replace(badge_nums.group(0), "")

        birthday_match = re.search(r"birthday:([0-9-]{10}) ?", query)
        birthday: str | None = None
        if birthday_match:
            query = query.replace(birthday_match.group(0), "")
            birthday = birthday_match.group(1)

        query = query.strip()

        return SearchFields(query=query, birthday=birthday)


@require_safe
@staff_member_required
def onsite_admin_search(request):
    event = Event.objects.get(default=True)
    query = request.GET.get("search", None)
    if query is None:
        return redirect("registration:onsite_admin")

    data = []

    def collect_badges(badges):
        for badge in badges:
            data.append(
                {
                    "id": badge.id,
                    "editUrl": reverse("admin:registration_badge_change", args=(badge.id,)),
                    "attendee": {
                        "firstName": badge.attendee.firstName,
                        "lastName": badge.attendee.lastName,
                        "preferredName": badge.attendee.preferredName,
                    },
                    "badgeName": badge.badgeName,
                    "badgeNumber": badge.badgeNumber,
                    "abandoned": badge.abandoned,
                }
            )

    query = query.strip()

    fields = SearchFields.parse(query)

    if fields.badge_ids:
        badges = Badge.objects.filter(event=event, badgeNumber__in=fields.badge_ids)
        collect_badges(badges)

    full_name = Func(
        F("attendee__firstName"), Value(" "), F("attendee__lastName"), function="CONCAT"
    )
    greater_similarity = Func("name_similarity", "badge_similarity", function="GREATEST")

    filters = (
        Q(name_similarity__gte=0.4)
        | Q(badge_similarity__gte=0.6)
        | Q(attendee__lastName__iexact=fields.query)
    )

    if fields.birthday:
        filters = filters & Q(attendee__birthdate=fields.birthday)

    results = (
        Badge.objects.annotate(
            name_similarity=TrigramSimilarity(full_name, fields.query),
            badge_similarity=TrigramSimilarity("badgeName", fields.query),
        )
        .filter(Q(event=event) & filters)
        .order_by(greater_similarity)
        .reverse()
        .prefetch_related("attendee")[:50]
    )

    collect_badges(results)

    return JsonResponse({"success": True, "results": data})


def update_terminal_status(request, status: str) -> JsonResponse:
    active = get_terminal_from_request(request)
    if not active:
        return JsonResponse(
            {"success": False, "reason": "No terminal associated with request"},
            status=400,
        )

    return send_mqtt_message_to_terminal(
        active,
        "payment/state",
        status,
    )


@require_POST
@staff_member_required
def set_terminal_status(request):
    status = request.GET.get("status", "close")
    return update_terminal_status(request, status)


def get_terminal_from_request(request) -> Firebase | None:
    url_terminal = request.GET.get("terminal", None)
    session_terminal = request.session.get("terminal", None)

    active = None

    if url_terminal:
        try:
            candidate = Firebase.objects.get(id=int(url_terminal))
        except (ValueError, Firebase.DoesNotExist):
            return None
        # MED-2: a URL/POST `terminal` param may bind the session only
        # with a valid signed terminal-token proof for that terminal.
        # Without proof, ignore the param and fall back to an already
        # (proven) session terminal — the param alone can't flip context.
        if _request_proves_terminal(request, candidate):
            active = candidate
            request.session["terminal"] = active.id
        else:
            _security_logger.warning(
                "terminal bind rejected: no valid terminal-token proof (get_terminal_from_request)"
            )

    if not active and session_terminal:
        try:
            active = Firebase.objects.get(id=int(session_terminal))
        except Firebase.DoesNotExist:
            return None

    return active


def send_mqtt_message_to_terminal(
    request: HttpRequest | Firebase, topic: str, data=None
) -> JsonResponse:
    if data is None:
        data = {}
    active: Firebase | None
    if isinstance(request, Firebase):
        active = request
    else:
        active = get_terminal_from_request(request)
        if not active:
            return JsonResponse(
                {"sucess": False, "reason": "No terminal associated with request"},
                status=400,
            )

    topic = mqtt.get_topic(topic, name=str(active.name))

    try:
        mqtt.send_mqtt_message(topic, data)
    except Exception as ex:
        logger.error("could not send mqtt message: %s", ex)
        return JsonResponse({"success": False, "reason": "Could not send MQTT message"}, status=500)

    return JsonResponse({"success": True})


@require_POST
@staff_member_required
def enable_payment(request):
    cart = request.session.get("cart", None)
    if cart is None:
        request.session["cart"] = []
        return JsonResponse({"success": False, "reason": "Cart not initialized"}, status=200)

    badges = []
    first_order = None

    for pk in cart:
        try:
            badge = Badge.objects.get(id=pk)
            badges.append(badge)

            order = badge.getOrder()
            if first_order is None:
                first_order = order
            else:
                # FIXME: use order.onsite_reference instead.
                # FIXME: Put this in cash handling, too
                # Reassign order references of items in cart to match first:
                order = badge.getOrder()
                order.reference = first_order.reference
                order.save()
        except Badge.DoesNotExist:
            cart.remove(pk)
            logger.error(f"ID {pk} was in cart but doesn't exist in the database")

    # Force a cart refresh to get the latest order reference to the terminal
    onsite_admin_cart(request)

    data = build_result(cart)

    terminal = get_terminal_from_request(request)
    if not terminal:
        return JsonResponse({"sucess": False, "reason": "No terminal associated with request."})

    order_id = payments.create_square_order(str(terminal.name), data)

    if (
        terminal.payment_type == Firebase.SQUARE_TERMINAL
        or request.GET.get("fallback", None) == "true"
    ) and terminal.square_terminal_id:
        api_response = payments.prompt_terminal_payment(
            request,
            str(terminal.square_terminal_id),
            int(data["total"] * 100),
            data["reference"],
            render_to_string("registration/customer-note.txt", data),
            order_id,
        )

        if api_response.checkout:
            return JsonResponse(
                {
                    "success": True,
                }
            )
        else:
            return JsonResponse(
                {
                    "success": False,
                    "reason": ", ".join([str(error.detail) for error in api_response.errors or []]),
                }
            )
    elif terminal.payment_type == Firebase.MQTT_REGISTER_APP:
        return send_mqtt_message_to_terminal(
            terminal,
            "payment/process",
            {
                "paymentAttemptId": payments.get_idempotency_key(request),
                "orderId": order_id,
                "total": int(data["total"] * 100),
                "reference": data["reference"],
                "note": render_to_string("registration/customer-note.txt", data),
            },
        )
    else:
        return JsonResponse(
            {
                "success": False,
                "reason": "Terminal does not have payment type",
            }
        )


@require_POST
@staff_member_required
def assign_badge_number(request):
    request_badges = json.loads(request.body)

    badge_payload = {badge["id"]: badge for badge in request_badges}

    badge_set = Badge.objects.filter(id__in=list(badge_payload.keys()))

    admin.assign_badge_numbers(None, request, badge_set)
    errors = get_messages_list(request)
    if errors:
        return JsonResponse(
            {"success": False, "errors": errors, "reason": "\n".join(errors)},
            status=400,
        )
    return JsonResponse({"success": True})


def get_messages_list(request):
    storage = get_messages(request)
    return [message.message for message in storage]


@require_POST
@staff_member_required
def onsite_print_badges(request):
    badge_list = request.GET.getlist("id")
    terminal = get_active_terminal(request)

    # Peer-review ATTACK-1: distinct salt so this print-capability blob
    # can never be replayed as a terminal-token cookie.
    data = print_capability_signer().sign_object(
        {
            "badge_ids": [int(badge_id) for badge_id in badge_list],
            "terminal": terminal.name if terminal else None,
            "source": PrintHistory.ONSITE,
        }
    )

    pdf_path = reverse("registration:pdf") + f"?data={data}"
    print_url = reverse("registration:print") + "?" + urlencode({"file": pdf_path})

    return JsonResponse(
        {
            "success": True,
            "next": request.get_full_path(),
            "file": pdf_path,
            "url": print_url,
        }
    )


def admin_push_cart_refresh(request):
    send_mqtt_message_to_terminal(request, "web/refresh")


# TODO: update for square SDK data type (fetch txn from square API and store in order.apiData)
@csrf_exempt
def complete_square_transaction(request):
    try:
        token = request.headers.get("authorization").removeprefix("Bearer ")
    except Exception:
        return JsonResponse({"success": False, "reason": "Invalid authorization"}, status=401)

    # Decision #8 / RT-B1: tokens are stored hash-only (no plaintext
    # column). find_by_token hashes the presented bearer and does an
    # indexed lookup; a miss returns the generic 401 below — no
    # token-existence oracle. This is NOT a constant-time comparison and
    # makes no such claim; hashed-at-rest + generic 401 is the control.
    terminal = Firebase.find_by_token(token)
    if terminal is None:
        return JsonResponse(
            {
                "success": False,
                "reason": "Unknown token",
            },
            status=401,
        )
    request.session["terminal"] = terminal.id

    data = json.loads(request.body)

    reference = data.get("reference")
    paymentId = data.get("paymentId")

    if not reference or not paymentId:
        return JsonResponse(
            {
                "success": False,
                "reason": "reference and transactionId are required parameters",
            },
            status=400,
        )

    # Things we need:
    #   orderID or reference (passed to square by metadata)
    # Square returns:
    #   clientTransactionId (offline payments)
    #   serverTransactionId (online payments)

    try:
        orders = Order.objects.filter(reference=reference).prefetch_related()
    except Order.DoesNotExist:
        logger.error(f"No order matching reference {reference}")
        return JsonResponse(
            {
                "success": False,
                "reason": "No order matching the reference specified exists",
            },
            status=404,
        )

    combine_orders(orders)

    store_api_data = {}

    order = orders[0]

    # S33 HIGH-2 (OWASP API1 BOLA): if this order was opened at a specific
    # terminal, only that terminal may complete it. Fail-safe — the guard is
    # inert for legacy/null orders, so it cannot break an existing checkout.
    # Collapse to a generic 404 to avoid a cross-terminal order oracle.
    if order.opened_at_terminal_id and order.opened_at_terminal_id != terminal.id:
        return JsonResponse({"success": False, "reason": "Not found"}, status=404)

    # Lookup the payment(s?) associated with this order:
    if paymentId:
        store_api_data["payment"] = {"id": paymentId}
    else:
        order.notes = "No paymentId."

    order.billingType = Order.CREDIT
    order.settledDate = timezone.now()
    order.apiData = sanitize_api_data(json.dumps(store_api_data))  # MED-8

    # Peer-review BLOCK-3 / S24: route the onsite-credit completion
    # through the fused CAS primitive (status+capacity, atomic,
    # idempotent vs a concurrent Square webhook) instead of a raw
    # status flip + save that skipped capacity and could clobber a
    # parallel webhook winner.
    won = payments.transition_order_status(
        order,
        Order.COMPLETED,
        # Onsite (pay-at-door) orders start at ONSITE_PENDING; a Square
        # capture-then-complete may also arrive PENDING/CAPTURED. Omitting
        # ONSITE_PENDING here silently dropped every onsite credit
        # completion (status stuck, capacity unconsumed, billingType
        # never set) — the S24 BLOCK-3 migration regression CI caught.
        expected=[Order.PENDING, Order.CAPTURED, Order.ONSITE_PENDING],
        extra_fields={
            "billingType": Order.CREDIT,
            "settledDate": order.settledDate,
            "notes": order.notes,
            "apiData": order.apiData,
        },
        refresh=False,
    )
    # Peer-review R2/R3 (Adversarial / Blue Team F5): the CAS owns the
    # status+capacity transition. Only the WINNER reconciles with Square
    # via refresh_payment. On a CAS LOSS a parallel Square webhook
    # already finalized this order (status + capacity + apiData), so
    # re-running refresh_payment here would be a non-CAS full-row write
    # racing/clobbering that winner on apiData/total for no benefit —
    # skip it and report success (the order IS completed). On a WIN,
    # refresh_payment runs with update_capacity=False (the CAS already
    # moved capacity exactly once — re-applying it was the BLOCK-3
    # double-decrement).
    if won:
        # In-memory mirror of the CAS we just won (no DB write here).
        order.status = Order.COMPLETED  # status-writer-ok: in-memory CAS mirror
        if paymentId:
            status, errors = payments.refresh_payment(order, store_api_data, update_capacity=False)
            if not status:
                return JsonResponse({"success": False, "error": errors}, status=210)

    admin_push_cart_refresh(request)

    return JsonResponse({"success": True})


def combine_orders(orders):
    # If there is more than one order, we should flatten them into one by reassigning all these
    # orderItems to the first order, and delete the rest.
    first_order = orders[0]

    if len(orders) > 1:
        order_items = []

        for order in orders[1:]:
            order_items += order.orderitem_set.all()
            first_order.notes += (
                f"\n[Combined from order reference {order.reference}]\n{order.notes}\n"
            )

        for order_item in order_items:
            old_order = order_item.order
            order_item.order = first_order
            if old_order and old_order.id:
                logger.warning(f"Deleting old order id={old_order.id}")
                old_order.delete()
            order_item.save()

        first_order.save()


@require_safe
@staff_member_required
@permission_required("order.cash_admin")
def drawer_status(request):
    if Cashdrawer.objects.count() == 0:
        return JsonResponse({"success": False})
    total = Cashdrawer.objects.all().aggregate(Sum("total"))
    drawer_total = Decimal(total["total__sum"])
    if drawer_total == 0:
        status = "CLOSED"
    elif drawer_total < 0:
        status = "SHORT"
    elif drawer_total > 0:
        status = "OPEN"
    return JsonResponse({"success": True, "total": drawer_total, "status": status})


@require_POST
@staff_member_required
@permission_required("order.cash_admin")
def no_sale(request):
    position = get_active_terminal(request)
    # MED-2: no proven terminal → nothing to print to; the no-sale event
    # is itself non-financial, so just skip the receipt push.
    if position is not None:
        mqtt.send_mqtt_message(mqtt.get_topic("receipt/nosale", name=str(position.name)))
    else:
        _security_logger.warning("no-sale receipt not pushed: no proven terminal bound to session")

    _audit_cash_action(request, "no_sale", terminal=position)
    return JsonResponse({"success": True})


@staff_member_required
@permission_required("order.cash_admin")
def print_audit_receipt(request, audit_type, cash_ledger, cashdraw=True):
    position = get_active_terminal(request)
    # MED-2: the cash-drawer ledger entry is already persisted by the
    # caller (audited regardless); the audit *slip* just needs a print
    # target. With no proven terminal, skip the push instead of 500-ing a
    # completed, permission-gated drawer action.
    if position is None:
        _security_logger.warning("audit-slip not pushed: no proven terminal bound to session")
        return
    event = Event.objects.get(default=True)
    payload = {
        "v": 1,
        "event": event.name,
        "terminal": position.name,
        "type": audit_type,
        "amount": abs(cash_ledger.total),
        "user": request.user.username,
        "timestamp": cash_ledger.timestamp.isoformat(),
        "cashdraw": cashdraw,
    }

    mqtt.send_mqtt_message(mqtt.get_topic("receipt/auditslip", name=str(position.name)), payload)


def cash_audit_action(request, action):
    cashdraw = True
    amount = Decimal(request.POST.get("amount", None))
    position = get_active_terminal(request)
    if action in (Cashdrawer.DROP, Cashdrawer.PICKUP, Cashdrawer.CLOSE):
        amount = -abs(amount)
        cashdraw = False
    cash_ledger = Cashdrawer(action=action, total=amount, user=request.user, position=position)
    cash_ledger.save()
    cash_ledger.refresh_from_db()
    _audit_cash_action(request, action, amount=amount, terminal=position)
    print_audit_receipt(request, action, cash_ledger, cashdraw)

    return JsonResponse({"success": True})


@require_POST
@staff_member_required
@permission_required("order.cash_admin")
def open_drawer(request):
    return cash_audit_action(request, Cashdrawer.OPEN)


@require_POST
@staff_member_required
@permission_required("order.cash_admin")
def cash_deposit(request):
    return cash_audit_action(request, Cashdrawer.DEPOSIT)


@require_POST
@staff_member_required
@permission_required("order.cash_admin")
def safe_drop(request):
    return cash_audit_action(request, Cashdrawer.DROP)


@require_POST
@staff_member_required
@permission_required("order.cash_admin")
def cash_pickup(request):
    return cash_audit_action(request, Cashdrawer.PICKUP)


@require_POST
@staff_member_required
@permission_required("order.cash_admin")
def close_drawer(request):
    return cash_audit_action(request, Cashdrawer.CLOSE)


def cash_receipt_payload(order: Order, tendered: str, total: str) -> dict:
    order_items = OrderItem.objects.filter(order=order)
    attendee_options = [
        line_item
        for item in order_items
        for line_item in get_line_items(item.attendeeoptions_set.all())
    ]

    # discounts
    if order.discount:
        if order.discount.amountOff:
            attendee_options.append({"item": "Discount", "price": f"-${order.discount.amountOff}"})
        elif order.discount.percentOff:
            attendee_options.append({"item": "Discount", "price": f"-%{order.discount.percentOff}"})

    event = Event.objects.get(default=True)
    payload: dict[str, Any] = {
        "v": 1,
        "event": event.name,
        "line_items": attendee_options,
        "donations": {"org": {"name": event.name, "price": str(order.orgDonation)}},
        "total": order.total,
        "payment": {
            "type": order.billingType,
            "tendered": Decimal(tendered),
            "change": Decimal(tendered) - Decimal(total),
            "details": f"Ref: {order.reference}",
        },
        "reference": order.reference,
    }

    if event.charity:
        payload["donations"]["charity"] = (
            {"name": event.charity.name, "price": str(order.charityDonation)},
        )

    return payload


@require_POST
@staff_member_required
@permission_required("order.cash")
def complete_cash_transaction(request):
    reference = request.GET.get("reference", None)
    total = request.GET.get("total", None)
    tendered = request.GET.get("tendered", None)

    if reference is None or tendered is None or total is None:
        return JsonResponse(
            {
                "success": False,
                "reason": "Reference, tendered, and total are required parameters",
            },
            status=400,
        )

    try:
        orders = Order.objects.filter(reference=reference).prefetch_related()
    except Order.DoesNotExist:
        return JsonResponse(
            {
                "success": False,
                "reason": "No order matching the reference specified exists",
            },
            status=404,
        )

    combine_orders(orders)

    order = orders[0]

    # S33 HIGH-2 (OWASP API1 BOLA): if the order is bound to a terminal and
    # the active session terminal is resolvable and differs, reject. Cash is
    # already @staff_member_required + @permission_required("order.cash");
    # if the active terminal can't be resolved we fail open (skip the guard)
    # rather than block a permitted staff cash sale on a session edge case.
    active_terminal = get_active_terminal(request)
    if (
        order.opened_at_terminal_id
        and active_terminal is not None
        and order.opened_at_terminal_id != active_terminal.id
    ):
        _audit_cash_action(
            request,
            "cash_sale",
            amount=total,
            terminal=active_terminal,
            reference=reference,
            outcome="rejected_terminal_mismatch",
        )
        return JsonResponse({"success": False, "reason": "Not found"}, status=404)

    # Peer-review BLOCK-3 / S24: a raw status flip + save here skipped the
    # capacity transition entirely (a cash sale of a capped price level
    # never consumed a slot) and could clobber a concurrent webhook. Route
    # through the fused CAS primitive: status + capacity, atomic,
    # idempotent (a duplicate cash submit no-ops instead of double-selling).
    order.billingType = Order.CASH
    order.settledDate = timezone.now()
    order.notes = json.dumps({"type": "cash", "tendered": tendered})
    payments.transition_order_status(
        order,
        Order.COMPLETED,
        # Onsite cash sale: the order is created at ONSITE_PENDING (pay at
        # the door). Omitting it dropped every onsite cash completion —
        # the till took the money but the order never moved to COMPLETED
        # and capacity was never consumed. S24 BLOCK-3 regression.
        expected=[Order.PENDING, Order.CAPTURED, Order.ONSITE_PENDING],
        extra_fields={
            "billingType": Order.CASH,
            "settledDate": order.settledDate,
            "notes": order.notes,
        },
        refresh=False,
    )
    _audit_cash_action(
        request,
        "cash_sale",
        amount=total,
        terminal=active_terminal,
        reference=reference,
    )

    txn = Cashdrawer(
        action=Cashdrawer.TRANSACTION, total=total, tendered=tendered, user=request.user
    )
    txn.save()

    payload = cash_receipt_payload(order, tendered, total)

    # MED-2: with the bind-gate, no session terminal may be resolvable
    # (e.g. a console without a valid terminal-token proof). The cash sale
    # itself is already recorded; only the receipt-print push needs a
    # terminal. Skip the push (log, don't 500) rather than fail a
    # completed, permitted, audited cash transaction on a missing receipt
    # target.
    if active_terminal is not None:
        mqtt.send_mqtt_message(
            mqtt.get_topic("receipt/print/cash", name=str(active_terminal.name)),
            payload,
        )
    else:
        _security_logger.warning("cash receipt not pushed: no proven terminal bound to session")

    return JsonResponse({"success": True})


def get_discount_dict(discount):
    if discount:
        reason = "\n\n---\n\n".join(filter(None, [discount.reason, discount.notes]))

        return {
            "name": discount.codeName,
            "percent_off": discount.percentOff,
            "amount_off": discount.amountOff,
            "id": discount.id,
            "valid": discount.isValid(),
            "status": discount.status,
            "reason": reason,
        }

    return None


def get_line_items(attendee_options: Iterable[AttendeeOptions]):
    out = []
    for option in attendee_options:
        option_dict = {
            "id": option.id,
            "item": option.option.optionName,
            "price": option.option.optionPrice,
            "quantity": 1,
            "total": option.option.optionPrice,
            "optionExtraType": option.option.optionExtraType,
            "optionValue": option.optionValue,
            "requiresFulfillment": option.option.requires_fulfillment,
            "fulfilledAt": option.fulfilled_at,
        }

        if option.option.optionExtraType == "int":
            val = Decimal(option.optionValue)
            option_dict["quantity"] = int(val)
            option_dict["total"] = option.option.optionPrice * val

        out.append(option_dict)
    return out


def build_result(cart):
    badges = []
    for pk in cart:
        try:
            badge = Badge.objects.get(id=pk)
            badges.append(badge)
        except Badge.DoesNotExist:
            cart.remove(pk)
            logger.error(f"ID {pk} was in cart but doesn't exist in the database")

    order = None
    subtotal = 0
    total_discount = 0
    result = []
    orders = set()
    for badge in badges:
        oi = badge.getOrderItems()
        level = None
        level_subtotal = 0
        attendee_options = []
        effectiveLevel = None
        for item in oi:
            level = item.priceLevel
            attendee_options.extend(get_line_items(item.getOptions()))
            level_subtotal += get_order_item_option_total(item.getOptions())

            if level:
                effectiveLevel = {"name": level.name, "price": level.basePrice}
                level_subtotal += level.basePrice

        subtotal += level_subtotal

        order = badge.getOrder()
        orders.add(order)

        holdType = None
        if badge.attendee.holdType:
            holdType = badge.attendee.holdType.name

        level_discount = (
            Decimal(get_discount_total(order.discount, level_subtotal) * 100) * TWOPLACES
        )
        total_discount += level_discount

        staff_data = None

        if badge.abandoned == Badge.STAFF:
            staff = Staff.objects.get(event=badge.event, attendee=badge.attendee)

            staff_data = {
                "shirtSize": staff.shirtsize.name if staff.shirtsize else None,
                "beforeDeadline": order.createdDate <= badge.event.staffRegEnd,
            }

        item = {
            "id": badge.id,
            "orderId": order.id,
            "firstName": badge.attendee.preferredName or badge.attendee.firstName,
            "lastName": badge.attendee.lastName,
            "badgeName": badge.badgeName,
            "badgeNumber": badge.badgeNumber,
            "abandoned": badge.abandoned,
            "effectiveLevel": effectiveLevel,
            "discount": get_discount_dict(order.discount),
            "age": get_attendee_age(badge.attendee),
            "holdType": holdType,
            "level_subtotal": level_subtotal,
            "level_discount": level_discount,
            "level_total": level_subtotal - level_discount,
            "attendee_options": attendee_options,
            "printed": badge.printed,
            "reference": order.reference,
            "staff": staff_data,
        }
        result.append(item)

    total = subtotal
    paid = Decimal(0)

    charityDonation = 0
    orgDonation = 0

    for order in orders:
        total += order.orgDonation + order.charityDonation
        paid += (
            order.total
            if order.billingType != Order.UNPAID
            and order.status in (Order.CAPTURED, Order.COMPLETED)
            else 0
        )

        charityDonation += order.charityDonation
        orgDonation += order.orgDonation

    data = {
        "success": True,
        "result": result,
        "subtotal": subtotal,
        "total": total - total_discount,
        "total_discount": total_discount,
        "charityDonation": charityDonation,
        "orgDonation": orgDonation,
        "paid": paid,
    }

    if order is not None:
        data["order_id"] = order.id
        data["reference"] = order.reference
    else:
        data["order_id"] = None
        data["reference"] = None

    return data


@require_safe
@staff_member_required
def onsite_admin_cart(request):
    # Returns dataset to render onsite cart preview
    request.session["heartbeat"] = time.time()  # Keep session alive
    cart = request.session.get("cart", [])

    data = build_result(cart)

    terminal_data = {
        "badges": [
            {
                "id": badge["id"],
                "firstName": badge["firstName"],
                "lastName": badge["lastName"],
                "badgeName": badge["badgeName"],
                "effectiveLevel": {
                    "name": badge["effectiveLevel"]["name"],
                    "price": str(badge["level_subtotal"]),
                },
                "discountedPrice": str(badge["level_total"]),
            }
            for badge in data["result"]
        ],
        "charityDonation": str(data["charityDonation"]),
        "organizationDonation": str(data["orgDonation"]),
        "totalDiscount": str(data["total_discount"]),
        "total": str(data["total"]),
        "paid": str(data["paid"]),
    }

    send_mqtt_message_to_terminal(request, "payment/cart/update", terminal_data)

    return JsonResponse(data)


@require_POST
@staff_member_required
def onsite_add_to_cart(request):
    badge_ids = request.GET.getlist("id")
    assign = request.GET.get("assign") == "yes"

    try:
        badge_ids = [int(badge_id) for badge_id in badge_ids]
    except ValueError:
        return JsonResponse({"success": False, "reason": "Unexpected badge ID value"}, status=400)

    badges = Badge.objects.filter(id__in=badge_ids)

    if len(badge_ids) > 1:
        preserved = Case(*[When(pk=pk, then=pos) for pos, pk in enumerate(badge_ids)])
        badges = badges.order_by(preserved)

    cart = [] if assign else request.session.get("cart", [])

    for badge in badges:
        order_item = OrderItem.objects.filter(badge=badge, order__isnull=False).first()
        if order_item:
            order_items = OrderItem.objects.filter(order=order_item.order, badge__isnull=False)
            for order_item in order_items:
                if order_item.badge_id not in cart:
                    cart.append(order_item.badge_id)

    request.session["cart"] = cart

    return JsonResponse({"success": True, "cart": cart})


@require_POST
@staff_member_required
def onsite_remove_from_cart(request):
    badge_id = request.GET.get("id", None)
    try:
        badge_id = int(badge_id)
    except (TypeError, ValueError):
        return JsonResponse(
            {"success": False, "reason": "ID parameter must be integer"}, status=400
        )

    cart = request.session.get("cart", None)
    if cart is None:
        return JsonResponse({"success": False, "reason": "Cart is empty"})

    try:
        cart.remove(badge_id)
        request.session["cart"] = cart
    except ValueError:
        return JsonResponse({"success": False, "cart": cart, "reason": "Not in cart"})

    return JsonResponse({"success": True, "cart": cart})


@require_POST
@staff_member_required
def onsite_admin_clear_cart(request):
    request.session["cart"] = []
    send_mqtt_message_to_terminal(request, "payment/cart/clear")
    return JsonResponse({"success": True, "cart": []})


@require_POST
@staff_member_required
def onsite_admin_transfer_cart(request):
    terminal_id = request.GET.get("terminal_id")
    badge_ids = request.GET.getlist("badge_id")

    firebase = Firebase.objects.get(id=terminal_id)

    topic = mqtt.get_topic("web/transfer", name=str(firebase.name))
    mqtt.send_mqtt_message(
        topic,
        {
            "badgeIds": [int(badge_id) for badge_id in badge_ids],
        },
    )

    return JsonResponse({"success": True})


def get_b32_uuid():
    uid = base64.b32encode(uuid.uuid4().bytes).decode("ascii")
    return uid[:26]


@require_POST
@staff_member_required
@permission_required("order.discount")
def create_discount(request):
    discount_type = request.POST.get("type")
    notes = request.POST.get("notes") or None
    department = None
    if department := request.POST.get("department") or None:
        department = Department.objects.get(name=department)

    try:
        value = Decimal(request.POST.get("value"))
    except ValueError:
        return JsonResponse({"success": False, "reason": "Unknown value provided"})

    cart = request.session.get("cart", None)
    if not cart:
        return JsonResponse(
            {"success": False, "reason": "Cart not initialized or empty"}, status=400
        )

    amount_off = Decimal(0)
    percent_off = Decimal(0)

    match discount_type:
        case "Amount":
            amount_off = value
        case "Percent":
            percent_off = value

    notes = "\n\n".join(
        item for item in [notes, f"Applied by [{request.user}]"] if item is not None
    )

    discount = Discount(
        codeName=generate_discount_code(),
        percentOff=percent_off,
        amountOff=amount_off,
        startDate=timezone.now(),
        endDate=timezone.now() + timedelta(hours=1),
        notes=notes,
        oneTime=True,
        used=0,
        reason="Onsite admin discount",
        sponsoring_department=department,
    )
    discount.save()

    # Combine cart orders and apply discount to combined order
    badges = Badge.objects.filter(pk__in=cart)
    orders = [badge.getOrder() for badge in badges]
    combine_orders(orders)

    orders[0].discount = discount
    orders[0].save()

    return JsonResponse({"success": True})


@require_POST
@staff_member_required
def onsite_print_clear(request):
    id = request.GET.get("id", None)
    if id is None or id == "":
        return JsonResponse({"success": False, "reason": "Need ID parameter"}, status=400)

    try:
        id = int(id)
    except ValueError:
        return JsonResponse(
            {"success": False, "reason": "ID parameter must be integer"}, status=400
        )

    badge = Badge.objects.get(id=id)
    badge.printed = False
    badge.save()

    return JsonResponse({"success": True})


@require_POST
@staff_member_required
def regtoken(request):
    terminal = get_active_terminal(request)
    if not terminal:
        return JsonResponse(
            {"success": False, "reason": "No terminal attached to session"}, status=400
        )

    # Peer-review ATTACK-1/2/3: salt-namespaced, id+rotation-epoch bound,
    # and set as an HttpOnly+Secure+SameSite cookie SERVER-side (no longer
    # handed to JS to write a non-HttpOnly cookie an XSS could read).
    data = mint_terminal_token(terminal)
    # The authoritative cookie for browser clients is the HttpOnly one set
    # here (an XSS can no longer read it). `token` is still returned for a
    # non-browser kiosk client that provisions its own cookie — both
    # resolve through the same salted/id-bound context, so this is purely
    # back-compat, not a second trust path.
    response = JsonResponse({"success": True, "token": data})
    response.set_cookie(
        "terminal-token",
        data,
        max_age=_TERMINAL_TOKEN_MAX_AGE,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
    )
    return response


@require_safe
@staff_member_required
def attendee_details(request):
    id = request.GET.get("id", None)
    if id is None or id == "":
        return JsonResponse({"success": False, "reason": "Need ID parameter"}, status=400)

    try:
        id = int(id)
    except ValueError:
        return JsonResponse(
            {"success": False, "reason": "ID parameter must be integer"}, status=400
        )

    # BOLA (S33 HIGH-1 / OWASP API1): scope the badge lookup to the active
    # onsite event and collapse any miss to a generic 404 so a staff session
    # at one event cannot enumerate or read attendee PII from another event.
    event = Event.objects.get(default=True)
    try:
        attendee = Badge.objects.get(id=id, event=event).attendee
    except Badge.DoesNotExist:
        return JsonResponse({"success": False, "reason": "Not found"}, status=404)

    return JsonResponse(
        {
            "success": True,
            "attendee": {
                "firstName": attendee.firstName,
                "lastName": attendee.lastName,
                "preferredName": attendee.preferredName,
                "email": attendee.email,
                "phone": attendee.phone,
                "address1": attendee.address1,
                "address2": attendee.address2,
                "city": attendee.city,
                "state": attendee.state,
                "country": attendee.country,
                "postalCode": attendee.postalCode,
                "dob": attendee.birthdate,
            },
        }
    )


@csrf_exempt
def terminal_square_token(request):
    # Peer-review ATTACK-4: a missing/garbage Authorization header must
    # return the generic 401, not an unhandled AttributeError 500
    # (.removeprefix on None) — mirrors complete_square_transaction.
    try:
        key = request.headers.get("authorization").removeprefix("Bearer ")
    except (AttributeError, TypeError):
        return JsonResponse({"success": False, "reason": "Incorrect API key"}, status=401)

    # Decision #8 / RT-B1: see complete_square_transaction comment.
    terminal = Firebase.find_by_token(key)
    if terminal is None:
        return JsonResponse({"success": False, "reason": "Incorrect API key"}, status=401)

    base_url = "https://connect.squareup.com"
    if settings.SQUARE_ENVIRONMENT == "sandbox":
        base_url = "https://connect.squareupsandbox.com"

    scopes = ["MERCHANT_PROFILE_READ", "PAYMENTS_WRITE", "PAYMENTS_WRITE_IN_PERSON"]
    state = get_random_token(64)

    url = (
        f"{base_url}/oauth2/authorize?client_id={settings.SQUARE_APPLICATION_ID}"
        f"&state={state}&scope={'+'.join(scopes)}"
    )

    send_mqtt_message_to_terminal(
        terminal,
        "web/authorize/square",
        {
            "url": url,
            "state": state,
        },
    )

    return JsonResponse(True, safe=False)


@require_safe
@staff_member_required
def oauth_square(request):
    url_state = request.GET.get("state") or ""
    cookie_state = request.COOKIES.get("square_oauth_state") or ""

    # S16: one clear predicate (no double-negative). bool(url_state) rejects
    # empty/absent state; secrets.compare_digest is constant-time and
    # returns False for any cookie/url mismatch incl. an empty cookie
    # (ASVS V2.1.7 / V13.2.6 — anti-CSRF/auth token compare).
    state_ok = bool(url_state) and secrets.compare_digest(url_state, cookie_state)
    if not state_ok:
        return JsonResponse(
            {"success": False, "reason": "Saved state did not match URL state"},
            status=400,
        )

    code = request.GET.get("code")

    token = payments.client.o_auth.obtain_token(
        client_id=settings.SQUARE_APPLICATION_ID,
        client_secret=settings.SQUARE_APPLICATION_SECRET,
        grant_type="authorization_code",
        code=code,
    )

    send_mqtt_message_to_terminal(
        request,
        "payment/update/token",
        {
            "accessToken": token.access_token,
            "refreshToken": token.refresh_token,
        },
    )

    resp = HttpResponseRedirect(reverse("registration:onsite_admin"))
    resp.delete_cookie("square_oauth_state")
    return resp


@require_POST
@staff_member_required
def print_receipts(request):
    terminal = get_active_terminal(request)
    if not terminal:
        return JsonResponse(
            {"success": False, "reason": "No terminal attached to session"}, status=400
        )

    references = request.GET.getlist("reference", [])
    orders = Order.objects.filter(reference__in=references).prefetch_related()

    for order in orders:
        if order.billingType in (Order.UNPAID, Order.COMP):
            continue

        if order.billingType == Order.CASH:
            try:
                note_data = json.loads(order.notes)
            except Exception:
                return JsonResponse(
                    {"success": False, "reason": "Cash order was missing note data"}
                )

            payload = cash_receipt_payload(order, note_data["tendered"], order.total)
            topic = mqtt.get_topic("receipt/print/cash", name=str(terminal.name))
            mqtt.send_mqtt_message(topic, payload)

        elif order.billingType == Order.CREDIT:
            if not order.apiData or "payment" not in order.apiData:
                return JsonResponse(
                    {
                        "success": False,
                        "reason": "Missing payment data on credit transaction",
                    }
                )

            if not payments.print_payment_receipt(
                request, terminal.square_terminal_id, order.apiData["payment"]["id"]
            ):
                return JsonResponse(
                    {
                        "success": False,
                        "reason": "Got error attempting to print receipt",
                    }
                )

    return JsonResponse({"success": True})


@require_POST
@staff_member_required
def fulfill(request):
    attendee_option_id = request.POST.get("id")

    with transaction.atomic():
        try:
            attendee_option = (
                AttendeeOptions.objects.select_for_update().filter(pk=attendee_option_id).first()
            )
        except AttendeeOptions.DoesNotExist:
            return JsonResponse({"success": False, "reason": "Option ID is unknown"})

        if attendee_option.fulfilled_at:
            return JsonResponse({"success": False, "reason": "Option was already fulfilled"})

        if not attendee_option.option.requires_fulfillment:
            return JsonResponse({"success": False, "reason": "Option does not require fulfillment"})

        if attendee_option.orderItem.badge.effectiveLevel() == Badge.UNPAID:
            return JsonResponse(
                {
                    "success": False,
                    "reason": "Option cannot be fulfilled for unpaid order",
                }
            )

        attendee_option.fulfilled_at = timezone.now()
        attendee_option.fulfilled_by = request.user
        attendee_option.save()

    return JsonResponse({"success": True})
