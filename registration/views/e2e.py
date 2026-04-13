"""HTTP helpers used only by the Playwright E2E suite.

All views in this module are registered in :mod:`registration.urls` only when
``settings.E2E_MODE`` is true, so the surface area is inert for production and
for ``manage.py test`` runs that leave ``E2E_MODE`` unset.

There are deliberately no authentication checks here because the suite runs
against a throwaway local stack — never wire this module into a deployment
where ``E2E_MODE`` could be toggled on without also ensuring the server is
otherwise isolated.
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from registration import models
from registration.e2e import paypal_stub


def _guard():
    if not getattr(settings, "E2E_MODE", False):
        return HttpResponseForbidden("E2E_MODE is not enabled")
    return None


# Volatile tables wiped between tests. Reference tables (Event, PriceLevel,
# Discount, TableSize, BadgeTemplate, User, Group, Permission) stay intact
# so the seed fixture loaded at server startup remains valid.
_VOLATILE = (
    models.OrderItem,
    models.Order,
    models.Cart,
    models.AttendeeOptions,
    models.Badge,
    models.DealerAsst,
    models.Dealer,
    models.Staff,
    models.StaffInvite,
    models.Attendee,
    models.PaymentWebhookNotification,
)


@csrf_exempt
@require_POST
def reset(request):
    guard = _guard()
    if guard is not None:
        return guard
    for model in _VOLATILE:
        model.objects.all().delete()
    paypal_stub.reset_state()
    return JsonResponse({"reset": True})


@require_GET
def order_state(request, reference: str):
    guard = _guard()
    if guard is not None:
        return guard
    try:
        order = models.Order.objects.get(reference=reference)
    except models.Order.DoesNotExist:
        return JsonResponse({"found": False}, status=404)
    return JsonResponse(
        {
            "found": True,
            "reference": order.reference,
            "status": order.status,
            "total": str(order.total),
            "billingType": order.billingType,
            "lastFour": order.lastFour,
            "apiData": order.apiData,
            "notes": order.notes,
        }
    )


@csrf_exempt
@require_POST
def advance_clock(request):
    """No-op hook reserved for future time-travel needs."""
    guard = _guard()
    if guard is not None:
        return guard
    return JsonResponse({"ok": True})


@require_GET
def paypal_snapshot(request, paypal_order_id: str):
    guard = _guard()
    if guard is not None:
        return guard
    snapshot = paypal_stub.get_order_snapshot(paypal_order_id)
    return JsonResponse({"snapshot": snapshot})
