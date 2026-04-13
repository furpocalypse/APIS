import json
import logging
from decimal import Decimal
from json import JSONDecodeError
from typing import Optional

from django.conf import settings
from django.http import HttpRequest
from django.utils import timezone
from paypalserversdk.api_helper import APIHelper
from paypalserversdk.configuration import Environment
from paypalserversdk.controllers.orders_controller import OrdersController
from paypalserversdk.controllers.payments_controller import PaymentsController
from paypalserversdk.exceptions.api_exception import ApiException
from paypalserversdk.http.api_response import ApiResponse
from paypalserversdk.http.auth.o_auth_2 import ClientCredentialsAuthCredentials
from paypalserversdk.logging.configuration.api_logging_configuration import (
    LoggingConfiguration,
    RequestLoggingConfiguration,
    ResponseLoggingConfiguration,
)
from paypalserversdk.models.amount_breakdown import AmountBreakdown
from paypalserversdk.models.amount_with_breakdown import AmountWithBreakdown
from paypalserversdk.models.capture_status import CaptureStatus
from paypalserversdk.models.captured_payment import CapturedPayment
from paypalserversdk.models.checkout_payment_intent import CheckoutPaymentIntent
from paypalserversdk.models.error_details import ErrorDetails
from paypalserversdk.models.item import Item
from paypalserversdk.models.item_category import ItemCategory
from paypalserversdk.models.money import Money
from paypalserversdk.models.order import Order as PayPalOrder
from paypalserversdk.models.order_request import OrderRequest
from paypalserversdk.models.orders_capture import OrdersCapture
from paypalserversdk.models.purchase_unit import PurchaseUnit
from paypalserversdk.models.purchase_unit_request import PurchaseUnitRequest
from paypalserversdk.models.refund import Refund
from paypalserversdk.models.refund_request import RefundRequest
from paypalserversdk.models.refund_status import RefundStatus
from paypalserversdk.paypal_serversdk_client import PaypalServersdkClient
from prometheus_client import Histogram

from .models import Order as ApisOrder
from .payments import refund_cash_payment
from .types import TranslatedCartItem

PAYPAL_REQUESTS = Histogram(
    "paypal_requests", "HTTP requests to Paypal API", ["endpoint"]
)

client = PaypalServersdkClient(
    client_credentials_auth_credentials=ClientCredentialsAuthCredentials(
        o_auth_client_id=settings.PAYPAL_CLIENT_ID,
        o_auth_client_secret=settings.PAYPAL_CLIENT_SECRET,
    ),
    environment=(
        Environment.PRODUCTION
        if settings.PAYPAL_ENVIRONMENT.lower()[0] == "p"
        else Environment.SANDBOX
    ),
    logging_configuration=LoggingConfiguration(
        log_level=logging.INFO,
        request_logging_config=RequestLoggingConfiguration(log_body=settings.DEBUG),
        response_logging_config=ResponseLoggingConfiguration(
            log_headers=settings.DEBUG
        ),
    ),
)

logger = logging.getLogger("registration.paypal_payments")


orders_controller: OrdersController = client.orders
payments_controller: PaymentsController = client.payments

if getattr(settings, "E2E_MODE", False):
    from registration.e2e import paypal_stub as _paypal_stub

    orders_controller = _paypal_stub.orders_controller
    payments_controller = _paypal_stub.payments_controller
    logger.warning("E2E_MODE active: PayPal controllers replaced with in-process stub")


def format_errors(api_response: ApiResponse) -> str:
    """
    Formats a list of Square API errors to lines of text.

    :param errors: A list of Square API errors.
    :return: Lines of text in the format of: ``<category> - <code>: <details>``
    """
    error_string = ""
    resp_dict: dict = json.loads(api_response.text)
    errors: list[ErrorDetails] = [
        ErrorDetails.from_dictionary(error) for error in resp_dict.get("errors", [])
    ]
    logger.debug(errors)
    for error in errors:
        error_string += error.issue
        if hasattr(error, "description"):
            error_string += f" - {error.description}"
        error_string += "\n"
    return error_string


def create_unpaid_paypal_order(
    total: str | int | Decimal,
    discount: str | int | Decimal,
    cart_items: list[TranslatedCartItem],
    apis_reference: str | None = None,
) -> ApiResponse:
    """Create an unpaid PayPal order.

    ``apis_reference`` is an internal correlation key set on the PayPal
    purchase unit as both ``invoice_id`` and ``custom_id``. PayPal mirrors
    these back on every downstream event (capture, refund, dispute), so
    webhook handlers can resolve to the local ``Order`` by matching
    ``Order.reference``. Callers must pass the same value that will become
    ``Order.reference`` when the Order row is later created by
    ``capture_paypal_payment``.
    """
    registrations: list[Item] = []
    donations: list[Item] = []
    donation_total = Decimal("0")
    for item in cart_items:
        is_donation = item.get("donation", False)
        pp_item = Item(
            name=item["name"],
            unit_amount=Money(currency_code="USD", value=str(item["total"])),
            quantity=1,
            category=(
                ItemCategory.DONATION if is_donation else ItemCategory.DIGITAL_GOODS
            ),
        )
        if is_donation:
            donations.append(pp_item)
            donation_total += Decimal(str(item["total"]))
        else:
            registrations.append(pp_item)

    discount_decimal = Decimal(str(discount))
    registration_total = Decimal(str(total)) - donation_total
    registration_amount = registration_total - discount_decimal

    purchase_units: list[PurchaseUnitRequest] = []

    # Registration purchase unit. Always emitted when registrations are
    # present; also emitted for an empty cart so the Orders API receives at
    # least one purchase_unit (and to preserve the legacy behavior of the
    # total==0 empty-cart call site).
    if registrations or not donations:
        registration_kwargs = dict(
            reference_id="registration",
            amount=AmountWithBreakdown(
                currency_code="USD",
                value=str(registration_amount),
                breakdown=AmountBreakdown(
                    item_total=Money(
                        currency_code="USD",
                        value=str(registration_amount),
                    ),
                    discount=Money(currency_code="USD", value=str(discount)),
                ),
            ),
            items=registrations,
        )
        if apis_reference:
            registration_kwargs["invoice_id"] = apis_reference
            registration_kwargs["custom_id"] = apis_reference
        purchase_units.append(PurchaseUnitRequest(**registration_kwargs))

    # Donation purchase unit — separate so PayPal reports DONATION category
    # accurately on receipts and in merchant reporting. PayPal requires
    # invoice_id to be unique across the merchant account, so suffix it;
    # custom_id is unconstrained and carries the unsuffixed reference so
    # webhook handlers resolve the local Order via the custom_id fallback.
    if donations:
        donation_kwargs = dict(
            reference_id="donation",
            amount=AmountWithBreakdown(
                currency_code="USD",
                value=str(donation_total),
                breakdown=AmountBreakdown(
                    item_total=Money(
                        currency_code="USD",
                        value=str(donation_total),
                    ),
                ),
            ),
            items=donations,
        )
        if apis_reference:
            donation_kwargs["invoice_id"] = f"{apis_reference}-don"
            donation_kwargs["custom_id"] = apis_reference
        purchase_units.append(PurchaseUnitRequest(**donation_kwargs))

    logger.debug("---- Begin PayPal Order Creation ----")

    api_response = orders_controller.create_order(
        {
            "body": OrderRequest(
                intent=CheckoutPaymentIntent.CAPTURE, purchase_units=purchase_units
            )
        }
    )
    logger.debug("---- PayPal Order Creation complete ----")
    logger.debug(api_response)
    return api_response


def capture_paypal_payment(
    paypal_order_id: int | str, apis_order: ApisOrder
) -> tuple[bool, dict]:
    body = {"id": paypal_order_id, "prefer": "return=representation"}
    logger.debug("---- Begin Capture ----")
    logger.debug(body)

    api_response: ApiResponse = None
    try:
        with PAYPAL_REQUESTS.labels(endpoint="capture_order").time():
            api_response = orders_controller.capture_order(body)
    except ApiException as ex:
        unknown_msg = "An unknown PayPal error occurred during payment capture"
        try:
            error = json.loads(ex.response.text)
        except (ValueError, JSONDecodeError):
            return False, {"errors": [unknown_msg]}
        return False, error.get("message", {"errors": [unknown_msg]})
    finally:
        logger.debug("---- Capture Completed ----")
        logger.debug(api_response)

    if api_response.is_error():
        logger.debug("---- Transaction Failed ----")
        errors = format_errors(api_response)
        apis_order.status = ApisOrder.FAILED
        apis_order.save()
        return False, {"errors": errors}

    try:
        apis_order.apiData = json.loads(api_response.text)
    except (ValueError, json.JSONDecodeError) as e:
        logger.exception(e)
        message = "Unable to decode response from PayPal in capture_paypal_payment"
        logger.error(message)
        return False, {"errors": [message]}

    order_data: PayPalOrder = PayPalOrder.from_dictionary(apis_order.apiData)

    if hasattr(order_data, "payment_source") and hasattr(
        order_data.payment_source, "card"
    ):
        apis_order.lastFour = order_data.payment_source.card.last_digits

    apis_order.status = ApisOrder.COMPLETED
    apis_order.notes = "PayPal: #" + order_data.id[:4]
    apis_order.save()

    logger.debug("---- End Transaction ----")

    return True, api_response.body


def refresh_payment(
    order: ApisOrder, store_api_data: dict = None
) -> tuple[bool, str | None]:
    """
    Queries the payment gateway to update payment information on an order.

    :param order: The :class:`Order` to update.
    :param store_api_data: Optional data. If not supplied, this function will
        pull from the ``apiData`` property of the ``order``.
    :return: A tuple of a boolean success status, and a string error if the
        success status is ``False``.
    """
    if store_api_data:
        api_data = store_api_data
    else:
        api_data = order.apiData
        if not api_data:
            logger.warning("No order data yet for {0}".format(order.reference))
            return False, "No order data yet for {0}".format(order.reference)
    order_total = 0

    try:
        api_data = PayPalOrder.from_dictionary(api_data)
        with PAYPAL_REQUESTS.labels(endpoint="refresh_order").time():
            refresh_response = orders_controller.get_order({"id": api_data.id})
    except ApiException as ex:
        try:
            error = json.loads(ex.response.text)
        except (ValueError, AttributeError):
            error = {}
        return False, error.get("message", "Unknown PayPal API error")
    except (AttributeError, IndexError, KeyError, TypeError):
        logger.warning("Refresh payment: MISSING_PAYMENT_ID")
        return False, "MISSING_PAYMENT_ID"

    if refresh_response.is_error():
        try:
            error = json.loads(refresh_response.text)
        except (ValueError, AttributeError):
            error = {}
        return False, error.get("message", "Unknown PayPal API error")

    try:
        fresh_order: PayPalOrder = refresh_response.body
        payment: CapturedPayment = fresh_order.purchase_units[0].payments.captures[0]
    except (AttributeError, IndexError, TypeError):
        return False, "Malformed payment data!"

    if hasattr(fresh_order, "payment_source") and hasattr(
        fresh_order.payment_source, "card"
    ):
        order.lastFour = fresh_order.payment_source.card.last_digits
    else:
        order.lastFour = ""

    order_total = update_order_payment_data(order, order_total, payment)

    message = None
    if getattr(fresh_order.purchase_units[0].payments, "refunds", None):
        order.total = order_total
        message = update_order_refund_data(
            order, fresh_order.purchase_units[0].payments.refunds
        )

    order.apiData = APIHelper.to_dictionary(fresh_order)
    order.total = order_total
    order.save()

    return message == None, message


def update_order_payment_data(
    order: ApisOrder, order_total: int, payment: CapturedPayment
) -> float:
    """
    Updates payment data in an APIS Order object based on info returned from
    the PayPal API.

    Based on given payment data, attempts to set the status of the order
    (``"COMPLETED"``, ``"FAILED"``, or ``"CAPTURED"``), and returns the amount
    that was charged. Does not save the Order.

    :param order: The :class:`Order` to be updated.
    :param order_total: The total amount charged.
    :param payment: Payment data returned from PayPal.
    :return: The total charge amount that should be logged. If the order failed,
             it will be the same as the ``order_total`` param. Otherwise, it
             will be the amount inside of the ``payment`` data.
    """
    status = payment.status
    if status == CaptureStatus.COMPLETED:
        order.status = ApisOrder.COMPLETED
        order_total = float(payment.amount.value)
    if status == CaptureStatus.DECLINED:
        order.status = ApisOrder.FAILED
    if status == CaptureStatus.PARTIALLY_REFUNDED:
        order.status = ApisOrder.REFUNDED
    if status == CaptureStatus.PENDING:
        order.status = ApisOrder.PENDING
        order_total = float(payment.amount.value)
    if status == CaptureStatus.REFUNDED:
        order.status = ApisOrder.REFUNDED
    if status == CaptureStatus.FAILED:
        order.status = ApisOrder.FAILED
    return order_total


def update_order_refund_data(order: ApisOrder, refunds: list[Refund]) -> str | None:
    """Updates the order status based on given refund data. The order will not
    be saved.

    :param order: The APIS :class:`Order` to update.
    :param refunds: A list of PayPal `Refund` models.
    :return: A message if side-effects happened (e.g. charity reset), None
        otherwise.
    """
    pending = [r for r in refunds if r.status == RefundStatus.PENDING]
    completed = [r for r in refunds if r.status == RefundStatus.COMPLETED]
    if pending:
        order.status = ApisOrder.REFUND_PENDING
    elif completed:
        order.status = ApisOrder.REFUNDED

    if order.orgDonation + order.charityDonation > order.total:
        order.orgDonation = 0
        order.charityDonation = order.total
        message = "Refunded order has caused charity and organization donation amounts to reset."
        logger.warning(message)
        order.notes += "\n{0}: {1}".format(timezone.now(), message)
        return message

    return None


def refund_payment(
    order: ApisOrder,
    amount: float,
    reason: Optional[str] = None,
    request: Optional[HttpRequest] = None,
) -> tuple[bool, str | None]:
    """
    Determines whether an order can be refunded, and processes the refund id so.

    Ripped wholesale from original payments.py until a more unified API can be
    figured out.

    :param order: The APIS :class:`Order` that the payment refund should belong to.
    :param amount: The amount being refunded.
    :param reason: The reason for the refund.
    :param request: Unused.
    :return: A Tuple of a `bool` and a `str`, indicating success status and a
             message in the case of an error.
    """

    if order.status == ApisOrder.FAILED:
        return False, "Failed orders cannot be refunded."
    if order.billingType == ApisOrder.CREDIT:
        result, message = refund_card_payment(order, amount, reason, request=None)
        return result, message
    if order.billingType == ApisOrder.CASH:
        result, message = refund_cash_payment(order, amount, reason)
        return result, message
    if order.billingType == ApisOrder.COMP:
        return False, "Comped orders cannot be refunded."
    if order.billingType == ApisOrder.UNPAID:
        return False, "Unpaid orders cannot be refunded."
    return False, "Not sure how to refund order type {0}!".format(order.billingType)


def refund_card_payment(
    order: ApisOrder,
    amount: float,
    reason: Optional[str] = None,
    request: Optional[HttpRequest] = None,
) -> tuple[bool, str]:
    """Process a refund for a card-based payment.

    :param order: The APIS :class:`Order` model being refunded.
    :param amount: The amount of money being refunded.
    :param reason: Optional reason to log for the refund.
    :param request: Original HTTP request from Django. Unused.
    :return: A tuple of a boolean success status and an accompanying message.
    """

    api_data = PayPalOrder.from_dictionary(order.apiData)
    if api_data == None:
        return (False, "APIS Order %s has malformed or missing order data!" % order.id)
    # All APIS-generated PayPal Orders will have one purchase unit and one
    # capture per purchase unit. Multiple captures only happen when sending
    # intent=AUTHORIZE to the Orders API and we don't do that.
    capture: OrdersCapture = None
    try:
        capture = api_data.purchase_units[0].payments.captures[0]
    except (AttributeError, IndexError, TypeError):
        return (False, "Can't find payment capture data for APIS Order %s!" % order.id)
    available = get_available_refund_amount(api_data)
    if available <= 0:
        return (
            False,
            "PayPal Order %s has already been refunded in full!" % api_data.id,
        )
    if amount > available:
        return (
            False,
            (
                "Refund on PayPal Order %s (%d) cannot exceed available "
                "refund amount %d!" % (api_data.id, amount, available)
            ),
        )
    args = {
        "id": capture.id,
        "prefer": "representation",
    }
    if amount < available:  # Full refund needs no body
        args["body"] = RefundRequest(
            amount=Money(currency_code=settings.PAYPAL_CURRENCY, value=str(amount)),
            note_to_payer=reason,
        )

    logger.debug("---- Begin Refund ----")
    logger.debug(args)

    try:
        with PAYPAL_REQUESTS.labels(endpoint="refund_payment").time():
            api_response = payments_controller.refund_captured_payment(args)
    except ApiException as e:
        logger.error("Error in PayPal refund: {0}".format(e.reason))
        api_response = e.response

    logger.debug("---- Refund Completed ----")
    logger.debug(api_response)

    resp_raw_body = json.loads(api_response.text)

    if api_response.is_error():
        logger.debug(resp_raw_body["errors"])
        message = resp_raw_body["errors"]
        logger.debug("---- Transaction Failed ----")
        return False, message

    refund: Refund = api_response.body
    status: RefundStatus = refund.status
    stored_refunds: list[Refund] = getattr(
        api_data.purchase_units[0].payments, "refunds", []
    )
    stored_refunds.append(refund)
    setattr(api_data.purchase_units[0].payments, "refunds", stored_refunds)
    order.apiData = APIHelper.to_dictionary(api_data)

    if status == RefundStatus.COMPLETED:
        order.status = ApisOrder.REFUNDED
    if status == RefundStatus.PENDING:
        order.status = ApisOrder.REFUND_PENDING

    if status in (RefundStatus.COMPLETED, RefundStatus.PENDING):
        order.total -= amount
        # Reset org & charity donations if the remaining total isn't enough to cover them:
        if order.orgDonation + order.charityDonation > order.total:
            order.orgDonation = 0
            order.charityDonation = order.total
            logger.warning(
                "Refunded order has caused charity and organization donation amounts to reset."
            )
            order.notes += "\nWarning: Refunded order has caused charity and organization donation amounts to reset.\n"

    if status in (RefundStatus.CANCELLED, RefundStatus.FAILED):
        order.status = ApisOrder.COMPLETED

    order.save()
    message = "PayPal refund has been submitted and is %s" % status
    logger.debug(message)
    return True, message


def get_available_refund_amount(order: PayPalOrder) -> float:
    """Calculates the amount of money available to be refunded from a given
    PayPal order purchase unit, based on captured payments and already-processed
    refunds.

    :param order: A PayPal Order model.
    :return: The total amount of money available
    """
    unit: PurchaseUnit = None
    capture: OrdersCapture = None
    refund: Refund = None
    total_captured: float = 0
    total_refunded: float = 0
    for unit in order.purchase_units:
        for capture in unit.payments.captures:
            if capture.status in [
                CaptureStatus.COMPLETED,
                CaptureStatus.PARTIALLY_REFUNDED,
                CaptureStatus.REFUNDED,
            ]:
                total_captured += float(capture.amount.value)
        if hasattr(unit.payments, "refunds"):
            for refund in unit.payments.refunds:
                if refund.status == RefundStatus.COMPLETED:
                    total_refunded += float(refund.amount.value)
    return total_captured - total_refunded
