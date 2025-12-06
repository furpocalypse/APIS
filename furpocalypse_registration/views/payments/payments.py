import logging
import uuid
from datetime import datetime

from django.conf import settings

from . import emails
from .models import *

# PAYPAL_TODO - Remove Square client, implement PayPal client
# DECISION: Online-only payments, complete Square removal
# Replace Square client initialization with PayPal SDK client for online payments only.
# No POS integration needed.
# References:
# - PayPal Orders API: https://developer.paypal.com/docs/api/orders/v2/
# - PayPal Server SDK: https://github.com/paypal/PayPal-server-sdk-python
# Related files: views/webhooks.py (PayPal client setup), models.py (Order model)
# client = Client(
#     access_token=settings.SQUARE_ACCESS_TOKEN,
#     environment=settings.SQUARE_ENVIRONMENT,
# )
# payments_api = client.payments
# refunds_api = client.refunds
# orders_api = client.orders

logger = logging.getLogger("registration.payments")


def get_idempotency_key(request=None):
    if request:
        header_key = request.META.get("IDEMPOTENCY_KEY")
        if header_key:
            return header_key
    return str(uuid.uuid4())


# PAYPAL_TODO - Implement PayPal charge_payment function
# This function was the main payment processing function for Square and needs to be
# completely rewritten for PayPal. Key requirements:
# 1. Create PayPal order using orders_controller.create_order() with OrderRequest
# 2. Handle PayPal payment capture flow using orders_controller.capture_order()
# 3. Update Order model with PayPal transaction data in apiData field
# 4. Map PayPal payment statuses to our Order.STATUS_CHOICES
# 5. Handle PayPal-specific error responses
# References:
# - PayPal Orders API: https://developer.paypal.com/docs/api/orders/v2/#orders_create
# - PayPal Capture API: https://developer.paypal.com/docs/api/orders/v2/#orders_capture
# - PayPal Server SDK: https://github.com/paypal/PayPal-server-sdk-python
# - Current PayPal implementation: views/webhooks.py (paypal_create_order, paypal_capture_order)
# Related files: views/ordering.py (checkout function), admin.py (order management)
def charge_payment(order, cc_data, request=None):
    """
    Process PayPal payment for an order.
    
    Args:
        order: Django Order model instance
        cc_data: Payment data including PayPal order ID and billing info
        request: HTTP request object (optional)
    
    Returns:
        tuple: (success_bool, response_data)
    """
    from .views.webhooks import get_orders_controller
    
    try:
        orders_controller = get_orders_controller()
        if not orders_controller:
            logger.error("PayPal orders controller not available")
            return False, {"errors": [{"detail": "PayPal service unavailable"}]}
        
        # Extract PayPal order ID from payment data
        paypal_order_id = cc_data.get("paypal_order_id")
        if not paypal_order_id:
            logger.error("Missing PayPal order ID in payment data")
            return False, {"errors": [{"detail": "Missing PayPal order ID"}]}
        
        # Store billing information
        order.billingPostal = cc_data.get("postal", "")
        
        # Capture the PayPal order
        logger.debug(f"Capturing PayPal order: {paypal_order_id}")
        capture_response = orders_controller.capture_order({
            "id": paypal_order_id,
            "prefer": "return=representation"
        })
        
        if capture_response.is_success():
            capture_data = capture_response.body
            order.apiData = capture_data
            
            # Extract payment information
            purchase_units = capture_data.get("purchase_units", [])
            if purchase_units:
                payments = purchase_units[0].get("payments", {})
                captures = payments.get("captures", [])
                
                if captures:
                    capture = captures[0]
                    capture_status = capture.get("status")
                    
                    # Map PayPal capture status to Order status
                    if capture_status == "COMPLETED":
                        order.status = Order.COMPLETED
                        order.notes = f"PayPal: #{capture.get('id', '')[:8]}"
                    elif capture_status == "PENDING":
                        order.status = Order.CAPTURED
                        order.notes = f"PayPal Pending: #{capture.get('id', '')[:8]}"
                    elif capture_status == "DECLINED":
                        order.status = Order.FAILED
                        order.notes = f"PayPal Declined: #{capture.get('id', '')[:8]}"
                    
                    # Store last 4 digits if available
                    payment_source = capture.get("payment_source", {})
                    if "card" in payment_source:
                        card_info = payment_source["card"]
                        order.lastFour = card_info.get("last_digits", "")
            
            order.save()
            logger.debug(f"PayPal order captured successfully: {paypal_order_id}")
            return True, capture_data
            
        else:
            # Handle capture failure
            logger.error(f"PayPal capture failed for order {paypal_order_id}: {capture_response.errors}")
            order.status = Order.FAILED
            order.apiData = {"errors": capture_response.errors}
            order.save()
            return False, {"errors": capture_response.errors}
            
    except Exception as e:
        logger.exception(f"Exception in PayPal charge_payment: {e}")
        order.status = Order.FAILED
        order.save()
        return False, {"errors": [{"detail": str(e)}]}


def format_errors(errors):
    error_string = ""
    for error in errors:
        error_string += "{e[category]} - {e[code]}: {e[detail]}\n".format(e=error)
    return error_string


def refresh_payment(order, store_api_data=None):
    """
    Refresh payment information from PayPal API.
    
    PAYPAL_TODO - Implement PayPal refresh_payment function
    This function refreshes payment information from the payment processor API.
    For PayPal, this needs to:
    1. Use PayPal Orders API to get current order status
    2. Use PayPal Payments API to get payment details
    3. Handle PayPal-specific refund information
    4. Update Order model with current PayPal data
    References:
    - PayPal Get Order: https://developer.paypal.com/docs/api/orders/v2/#orders_get
    - PayPal Payments API: https://developer.paypal.com/docs/api/payments/v2/
    Related files: admin.py (refresh_view function), models.py (Order.apiData)
    """
    raise NotImplementedError("PayPal refresh_payment function not yet implemented - Phase 2 feature")


# PAYPAL_TODO - Update payment data mapping for PayPal
# This function maps payment processor data to our Order model fields.
# Needs to be updated to handle PayPal capture object structure instead of Square.
# PayPal uses capture.status (COMPLETED, PENDING, DECLINED) vs Square payment.status.
# References:
# - PayPal Capture Object: https://developer.paypal.com/docs/api/orders/v2/#definition-capture
# - PayPal Order Status: https://developer.paypal.com/docs/api/orders/v2/#definition-order_status
# - PayPal Capture Status: https://developer.paypal.com/docs/api/orders/v2/#definition-capture_status
# Related files: models.py (Order.STATUS_CHOICES)
def update_order_payment_data(order, order_total, payment):
    """
    Update order with PayPal payment data.
    
    Args:
        order: Django Order model instance
        order_total: Current order total (for Square compatibility, may be None)
        payment: PayPal payment/capture data
    
    Returns:
        Updated order total or None
    """
    # Handle PayPal capture data structure
    try:
        # Try to extract card last 4 digits from PayPal payment source
        payment_source = payment.get("payment_source", {})
        if "card" in payment_source:
            card_info = payment_source["card"]
            order.lastFour = card_info.get("last_digits", "")
        elif "paypal" in payment_source:
            # PayPal wallet payments don't have card details
            order.lastFour = ""
    except (KeyError, AttributeError):
        logger.warning("Unable to update last_4 details for PayPal order")
    
    # Map PayPal capture status to Order status
    status = payment.get("status")
    if status == "COMPLETED":
        order.status = Order.COMPLETED
        # Extract amount from PayPal capture
        try:
            amount_value = payment["amount"]["value"]
            order_total = int(float(amount_value) * 100)  # Convert to cents
        except (KeyError, ValueError):
            logger.warning("Unable to extract amount from PayPal capture")
    elif status == "PENDING":
        order.status = Order.CAPTURED
        try:
            amount_value = payment["amount"]["value"]
            order_total = int(float(amount_value) * 100)  # Convert to cents
        except (KeyError, ValueError):
            logger.warning("Unable to extract amount from PayPal capture")
    elif status == "DECLINED":
        order.status = Order.FAILED
    elif status == "CANCELLED":
        order.status = Order.FAILED
    
    return order_total


# PAYPAL_TODO - Update webhook processing for PayPal
# This function processes PayPal webhook notifications instead of Square.
# PayPal webhook structure and event types are different from Square.
# References:
# - PayPal Webhooks: https://developer.paypal.com/docs/api/webhooks/v1/
# - PayPal Event Types: https://developer.paypal.com/docs/api/webhooks/v1/#webhooks_post
# Related files: views/webhooks.py (webhook endpoints), models.py (PaymentWebhookNotification)
def process_webhook_refund_updated(notification):
    """
    Process Square-style refund.updated webhook - NOT IMPLEMENTED for PayPal.
    
    PAYPAL_TODO - Remove Square webhook processing
    This function processes Square refund.updated webhooks which don't exist in PayPal.
    PayPal uses PAYMENT.CAPTURE.REFUNDED webhooks instead.
    This function should be removed during Phase 3 cleanup.
    """
    raise NotImplementedError("Square refund.updated webhook processing not implemented for PayPal - use PayPal webhooks instead")


def refund_payment(order, amount, reason=None, request=None):
    """
    Process refund for an order based on billing type.
    
    Args:
        order: Django Order model instance
        amount: Decimal amount to refund
        reason: Optional reason for refund
        request: HTTP request object (optional)
    
    Returns:
        tuple: (success_bool, message)
    """
    if order.status == Order.FAILED:
        return False, "Failed orders cannot be refunded."
    if order.billingType == Order.CREDIT:
        result, message = refund_card_payment(order, amount, reason, request)
        return result, message
    if order.billingType == Order.CASH:
        result, message = refund_cash_payment(order, amount, reason)
        return result, message
    if order.billingType == Order.COMP:
        return False, "Comped orders cannot be refunded."
    if order.billingType == Order.UNPAID:
        return False, "Unpaid orders cannot be refunded."
    return False, "Not sure how to refund order type {0}!".format(order.billingType)


def refund_cash_payment(order, amount, reason=None):
    # Change order status
    order.status = Order.REFUNDED
    order.notes += "\nRefund issued {0}: {1}".format(timezone.now(), reason)

    # Reset order total
    order.total -= amount
    order.save()

    # Record cashdrawer withdraw
    withdraw = Cashdrawer(action=Cashdrawer.TRANSACTION, total=-amount)
    withdraw.save()
    return True, None


# PAYPAL_TODO - Implement PayPal refund_card_payment function
# This function handles credit card refunds through PayPal instead of Square.
# Key requirements:
# 1. Extract PayPal capture ID from order.apiData["purchase_units"][0]["payments"]["captures"][0]["id"]
# 2. Use payments_controller.refunds().refund_capture() to process refund
# 3. Handle PayPal refund response and status (COMPLETED, PENDING, FAILED)
# 4. Update order with refund data in apiData["refunds"]
# 5. Map PayPal refund statuses to our Order status choices
# References:
# - PayPal Refunds API: https://developer.paypal.com/docs/api/payments/v2/#captures_refund
# - PayPal Refund Status: https://developer.paypal.com/docs/api/payments/v2/#definition-refund_status
# - PayPal Server SDK: https://github.com/paypal/PayPal-server-sdk-python
# Related files: admin.py (refund_view), models.py (Order.STATUS_CHOICES)
def refund_card_payment(order, amount, reason=None, request=None):
    """
    Process PayPal credit card refund.
    
    Args:
        order: Django Order model instance
        amount: Decimal amount to refund
        reason: Optional reason for refund
        request: HTTP request object (optional)
    
    Returns:
        tuple: (success_bool, message)
    """
    from .views.webhooks import get_payments_controller
    from paypalserversdk.models.refund_request import RefundRequest
    from paypalserversdk.models.money import Money
    from paypalserversdk.exceptions.error_exception import ErrorException
    
    try:
        payments_controller = get_payments_controller()
        if not payments_controller:
            logger.error("PayPal payments controller not available")
            return False, "PayPal service unavailable"
        
        api_data = order.apiData
        if not api_data:
            logger.error("No PayPal data available for refund")
            return False, "No payment data available for refund"
        
        # Extract PayPal capture ID
        try:
            capture_id = api_data["purchase_units"][0]["payments"]["captures"][0]["id"]
        except (KeyError, IndexError):
            logger.error("Unable to extract PayPal capture ID from order data")
            return False, "Unable to find PayPal capture ID"
        
        # Convert amount to cents for PayPal
        converted_amount = "{:.2f}".format(amount)
        
        # Create refund request
        refund_request = RefundRequest(
            amount=Money(
                currency_code="USD",
                value=converted_amount
            ),
            note_to_payer=reason or "Refund processed"
        )
        
        logger.debug(f"Processing PayPal refund for capture {capture_id}, amount {converted_amount}")
        
        # Process refund through PayPal
        result = payments_controller.refunds().refund_capture(
            capture_id=capture_id,
            body=refund_request
        )
        
        if result.is_success():
            refund_data = result.body
            logger.debug(f"PayPal refund successful: {refund_data}")
            
            # Store refund data
            stored_refunds = api_data.get("refunds", [])
            stored_refunds.append(refund_data)
            api_data["refunds"] = stored_refunds
            order.apiData = api_data
            
            # Map PayPal refund status to Order status
            refund_status = refund_data.get("status")
            if refund_status == "COMPLETED":
                order.status = Order.REFUNDED
            elif refund_status == "PENDING":
                order.status = Order.REFUND_PENDING
            elif refund_status in ("CANCELLED", "FAILED"):
                order.status = Order.COMPLETED
                order.save()
                return False, f"PayPal refund failed with status: {refund_status}"
            
            # Update order total and donations for successful refunds
            if refund_status in ("COMPLETED", "PENDING"):
                order.total -= amount
                # Reset org & charity donations if the remaining total isn't enough to cover them
                if order.orgDonation + order.charityDonation > order.total:
                    order.orgDonation = 0
                    order.charityDonation = order.total
                    logger.warning(
                        "Refunded order has caused charity and organization donation amounts to reset."
                    )
                    order.notes += "\nWarning: Refunded order has caused charity and organization donation amounts to reset.\n"
            
            order.save()
            message = f"PayPal refund has been submitted and is {refund_status}"
            logger.debug(message)
            return True, message
            
        else:
            # Handle refund failure
            logger.error(f"PayPal refund failed: {result.errors}")
            return False, f"PayPal refund failed: {result.errors}"
            
    except ErrorException as e:
        logger.exception(f"PayPal refund error: {e}")
        return False, f"PayPal refund error: {str(e)}"
    except Exception as e:
        logger.exception(f"Exception in PayPal refund_card_payment: {e}")
        return False, f"Refund processing error: {str(e)}"



# PAYPAL_TODO - Replace Square webhook processing with PayPal
# DECISION: Complete Square removal, PayPal webhooks only
# Square webhook functions will be removed and replaced with PayPal webhook processing.
# PayPal webhook event structure uses event_type field vs Square's type field.
# Focus on: PAYMENT.CAPTURE.COMPLETED, PAYMENT.CAPTURE.REFUNDED for online payments.
# References:
# - PayPal Webhook Events: https://developer.paypal.com/docs/api/webhooks/v1/#webhooks_post
# - PayPal Event Types: https://developer.paypal.com/docs/integration-guides/webhooks/
# - PayPal Webhook Structure: https://developer.paypal.com/docs/api/webhooks/v1/#definition-event
# Related files: views/webhooks.py (webhook endpoints)

def process_webhook_refund_update(notification) -> bool:
    # Find matching order based on refund ID:
    refund_id = notification.body["data"]["id"]
    try:
        order = Order.objects.get(apiData__refunds__contains=[{"id": refund_id}])
    except Order.DoesNotExist:
        logger.warning(
            f"Got refund.updated webhook update for a refund id not found: {refund_id}"
        )
        return False

    webhook_refund = notification.body["data"]["object"]["refund"]

    output = []
    refunds_list = order.apiData["refunds"]
    for refund in refunds_list:
        if refund["id"] == refund_id:
            output.append(webhook_refund)
        else:
            output.append(refund)

    if webhook_refund["status"] == "COMPLETED":
        order.status = Order.REFUNDED

    order.apiData["refunds"] = output
    order.save()
    return True


def process_webhook_payment_updated(notification: PaymentWebhookNotification) -> bool:
    payment_id = notification.body["data"]["id"]
    try:
        order = Order.objects.get(apiData__payment__id=payment_id)
    except Order.DoesNotExist:
        logger.warning(
            f"Got payment.updated webhook update for a payment id not found: {payment_id}"
        )
        return False

    # Store order update in api data
    payment = notification.body["data"]["object"]["payment"]
    order.apiData["payment"] = payment
    update_order_payment_data(order, None, payment)
    order.save()
    return True


def process_webhook_refund_created(notification: PaymentWebhookNotification) -> bool:
    # Find matching order based on refund ID:
    refund_id = notification.body["data"]["id"]
    webhook_refund = notification.body["data"]["object"]["refund"]
    payment_id = webhook_refund["payment_id"]
    try:
        order = Order.objects.get(apiData__payment__id=payment_id)
    except Order.DoesNotExist:
        logger.warning(
            f"Got refund.created webhook update for a payment id not found: {payment_id}"
        )
        return False

    # Skip processing if we already have this refund id stored:
    refund_exists = Order.objects.filter(apiData__refunds__contains=[{"id": refund_id}])
    if len(refund_exists) > 0:
        logger.info(f"Refund {refund_id} already exists, skipping processing...")
        return True

    # Store refund in api data

    order.apiData["refunds"].append(webhook_refund)

    status = webhook_refund["status"]
    if status == "COMPLETED":
        order.status = Order.REFUNDED
    if status == "PENDING":
        order.status = Order.REFUND_PENDING

    if status in ("COMPLETED", "PENDING"):
        order.total -= Decimal(webhook_refund["amount_money"]["amount"]) / 100
        # Reset org & charity donations if the remaining total isn't enough to cover them:
        if order.orgDonation + order.charityDonation > order.total:
            order.orgDonation = 0
            order.charityDonation = order.total
            logger.warning(
                "Refunded order has caused charity and organization donation amounts to reset."
            )
            order.notes += "\nWarning: Refunded order has caused charity and organization donation amounts to reset.\n"

    if status in ("REJECTED", "FAILED"):
        order.status = Order.COMPLETED

    order.save()
    return True


# PAYPAL_TODO - Implement PayPal dispute processing (Phase 2)
# DECISION: Replace Square dispute handling with PayPal dispute processing
# PayPal disputes use different statuses: OPEN, WAITING_FOR_BUYER_RESPONSE, etc.
# This function will be rewritten for PayPal dispute webhook events.
# Priority: Phase 2 (after core payment processing is restored)
# References:
# - PayPal Disputes API: https://developer.paypal.com/docs/api/customer-disputes/v1/
# - PayPal Dispute Webhooks: https://developer.paypal.com/docs/integration-guides/webhooks/
# Related files: models.py (Order.DISPUTE_STATUS_MAP), emails.py (chargeback notifications)
def process_webhook_dispute_created_or_updated(
    notification: PaymentWebhookNotification,
) -> bool:
    webhook_dispute = notification.body["data"]["object"]["dispute"]
    payment_id = webhook_dispute["disputed_payment"]["payment_id"]
    try:
        order = Order.objects.get(apiData__payment__id=payment_id)
    except Order.DoesNotExist:
        logger.warning(
            f"Got dispute.created webhook update for a payment id not found: {payment_id}"
        )
        return False

    # Add the dispute API data to the order:
    order.apiData["dispute"] = webhook_dispute
    order.status = Order.DISPUTE_STATUS_MAP[webhook_dispute["state"]]
    if order.status in (Order.DISPUTE_LOST, Order.DISPUTE_ACCEPTED) and (
        order.orgDonation > 0 or order.charityDonation > 0
    ):
        # If we've lost or accepted the dispute, reset charitable donation earmarks:
        order.notes += (
            f"\n\nOriginal charity donation of ${order.charityDonation} and organization donation "
            + f"of ${order.orgDonation} were reset due to lost or accepted dispute state."
        )
        order.orgDonation = 0
        order.charityDonation = 0
    order.save()

    # Place a hold on all new disputed orders, and add attendee to the ban list.  Should only do this once,
    # when the dispute is created (with state EVIDENCE_REQUIRED).
    if webhook_dispute["state"] == "EVIDENCE_REQUIRED":
        dispute_hold = get_hold_type("Chargeback")
        order_items = OrderItem.objects.filter(order=order)
        # Add dispute hold to all attendees on the order
        for oi in order_items:
            attendee = oi.badge.attendee
            attendee.holdType = dispute_hold
            attendee.save()

            # Add all attendees to the ban list
            ban = BanList(
                firstName=attendee.firstName,
                lastName=attendee.lastName,
                email=attendee.email,
                reason=f"Initiated chargeback [APIS {datetime.now().isoformat()}]",
            )

            ban.save()

            # Send an email about it
            emails.send_chargeback_notice_email(order)

    return True
