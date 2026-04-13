"""In-process replacements for the PayPal ``orders`` and ``payments``
controllers used by :mod:`registration.paypal_payments`.

Activated by ``settings.E2E_MODE``. The stubs persist order + capture + refund
state in process memory so that the capture and refund code paths exercise
realistic PayPal response shapes without leaving the Django process.
"""

from __future__ import annotations

import itertools
import json
import threading
from datetime import UTC, datetime

from paypalserversdk.http.api_response import ApiResponse
from paypalserversdk.http.http_response import HttpResponse
from paypalserversdk.models.order import Order as PayPalOrder
from paypalserversdk.models.refund import Refund

_lock = threading.Lock()
_order_seq = itertools.count(1)
_refund_seq = itertools.count(1)
_orders: dict[str, dict] = {}
_captures: dict[str, dict] = {}


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_order_id() -> str:
    with _lock:
        return f"E2EORDER{next(_order_seq):06d}"


def _next_capture_id() -> str:
    return f"E2ECAP{next(_order_seq):06d}"


def _next_refund_id() -> str:
    return f"E2EREF{next(_refund_seq):06d}"


def _wrap(body_dict: dict, body_type) -> ApiResponse:
    text = json.dumps(body_dict)
    http = HttpResponse(
        status_code=200,
        reason_phrase="OK",
        headers=[],
        text=text,
        request=None,
    )
    return ApiResponse(http, body_type.from_dictionary(body_dict))


def _extract_amount(purchase_units: list) -> str:
    if not purchase_units:
        return "0.00"
    amt = purchase_units[0].get("amount") or {}
    return str(amt.get("value") or "0.00")


class _OrdersStub:
    def create_order(self, request: dict) -> ApiResponse:
        body = request["body"]
        order_dict = _to_dict(body)
        purchase_units = order_dict.get("purchase_units") or []
        order_id = _next_order_id()
        stored = {
            "id": order_id,
            "status": "CREATED",
            "intent": order_dict.get("intent", "CAPTURE"),
            "purchase_units": purchase_units,
            "create_time": _now_iso(),
        }
        with _lock:
            _orders[order_id] = stored
        return _wrap(stored, PayPalOrder)

    def capture_order(self, request: dict) -> ApiResponse:
        order_id = request["id"]
        with _lock:
            stored = _orders.get(order_id)
        if stored is None:
            return _wrap(
                {
                    "id": order_id,
                    "status": "ERROR",
                    "purchase_units": [],
                },
                PayPalOrder,
            )

        units = stored.get("purchase_units") or []
        amount_value = _extract_amount(units)
        capture_id = _next_capture_id()
        capture = {
            "id": capture_id,
            "status": "COMPLETED",
            "amount": {"currency_code": "USD", "value": amount_value},
            "create_time": _now_iso(),
            "update_time": _now_iso(),
            "final_capture": True,
            "invoice_id": (units[0].get("invoice_id") if units else None),
            "custom_id": (units[0].get("custom_id") if units else None),
        }
        unit_copy = dict(units[0]) if units else {}
        unit_copy.setdefault("reference_id", "registration")
        unit_copy["payments"] = {"captures": [capture]}

        completed = {
            "id": order_id,
            "status": "COMPLETED",
            "intent": stored.get("intent", "CAPTURE"),
            "purchase_units": [unit_copy],
            "payment_source": {
                "card": {
                    "last_digits": "4242",
                    "brand": "VISA",
                    "type": "CREDIT",
                }
            },
            "create_time": stored.get("create_time", _now_iso()),
            "update_time": _now_iso(),
        }
        with _lock:
            _orders[order_id] = completed
            _captures[capture_id] = {
                "order_id": order_id,
                "amount": amount_value,
                "refunds": [],
            }
        return _wrap(completed, PayPalOrder)

    def get_order(self, request: dict) -> ApiResponse:
        order_id = request["id"]
        with _lock:
            stored = _orders.get(order_id)
        if stored is None:
            return _wrap(
                {"id": order_id, "status": "NOT_FOUND", "purchase_units": []},
                PayPalOrder,
            )
        return _wrap(stored, PayPalOrder)


class _PaymentsStub:
    def refund_captured_payment(self, request: dict) -> ApiResponse:
        capture_id = request["id"]
        req_body = request.get("body")
        req_dict = _to_dict(req_body) if req_body is not None else {}
        amount_obj = req_dict.get("amount") or {}

        with _lock:
            capture = _captures.get(capture_id)
            if capture is None:
                amount_value = str(amount_obj.get("value") or "0.00")
            else:
                amount_value = str(amount_obj.get("value") or capture.get("amount") or "0.00")

        refund_id = _next_refund_id()
        refund = {
            "id": refund_id,
            "status": "COMPLETED",
            "amount": {"currency_code": "USD", "value": amount_value},
            "note_to_payer": req_dict.get("note_to_payer"),
            "create_time": _now_iso(),
            "update_time": _now_iso(),
        }

        with _lock:
            if capture is not None:
                capture["refunds"].append(refund)
                _captures[capture_id] = capture
                order_id = capture["order_id"]
                stored = _orders.get(order_id)
                if stored:
                    unit = (stored.get("purchase_units") or [{}])[0]
                    payments = unit.setdefault("payments", {})
                    refunds = payments.setdefault("refunds", [])
                    refunds.append(refund)
                    _orders[order_id] = stored
        return _wrap(refund, Refund)


def _to_dict(obj) -> dict:
    """Best-effort dict conversion for SDK model objects or raw dicts."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dictionary"):
        return obj.to_dictionary()
    try:
        from paypalserversdk.api_helper import APIHelper

        return APIHelper.to_dictionary(obj)
    except Exception:
        return {}


orders_controller = _OrdersStub()
payments_controller = _PaymentsStub()


def reset_state() -> None:
    """Clear all in-memory PayPal state. Used between E2E tests."""
    global _order_seq, _refund_seq
    with _lock:
        _orders.clear()
        _captures.clear()
        _order_seq = itertools.count(1)
        _refund_seq = itertools.count(1)


def get_order_snapshot(order_id: str) -> dict | None:
    with _lock:
        stored = _orders.get(order_id)
        return dict(stored) if stored else None
