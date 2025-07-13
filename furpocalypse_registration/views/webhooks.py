# TODO: PayPal Integration - Complete PayPal webhook integration (Phase 1-2)
# DECISION: Online-only PayPal payments, complete Square removal
# Current PayPal implementation only includes basic order creation and capture.
# Missing critical components for online payment processing:
# 1. Integration with Django Order model and payment processing flow
# 2. PayPal webhook endpoints for online payment status updates
# 3. PayPal refund webhook processing (essential for admin)
# 4. PayPal dispute webhook handling (Phase 2)
# 5. Error handling and logging for PayPal API failures
# 6. PayPal environment configuration (sandbox vs production)
# References:
# - PayPal Webhooks Guide: https://developer.paypal.com/docs/api/webhooks/v1/
# - PayPal Event Types: https://developer.paypal.com/docs/integration-guides/webhooks/
# Related files: payments.py (webhook processing functions), models.py (PaymentWebhookNotification)

import json
import logging
import os

from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
# TODO: PayPal Integration - Remove Square webhook imports (Phase 3 - Cleanup)
# DECISION: Complete Square removal
# Square webhook validation is no longer needed and should be removed entirely.
# from square.utilities.webhooks_helper import is_valid_webhook_event_signature

from paypalserversdk.http.auth.o_auth_2 import ClientCredentialsAuthCredentials

from paypalserversdk.logging.configuration.api_logging_configuration import (
    LoggingConfiguration,
    RequestLoggingConfiguration,
    ResponseLoggingConfiguration,
)

from paypalserversdk.paypal_serversdk_client import PaypalServersdkClient
from paypalserversdk.controllers.orders_controller import OrdersController
from paypalserversdk.controllers.payments_controller import PaymentsController
from paypalserversdk.models.amount_breakdown import AmountBreakdown
from paypalserversdk.models.amount_with_breakdown import AmountWithBreakdown
from paypalserversdk.models.checkout_payment_intent import CheckoutPaymentIntent
from paypalserversdk.models.order_request import OrderRequest
from paypalserversdk.models.capture_request import CaptureRequest
from paypalserversdk.models.money import Money
from paypalserversdk.models.shipping_details import ShippingDetails
from paypalserversdk.models.shipping_option import ShippingOption
from paypalserversdk.models.shipping_type import ShippingType
from paypalserversdk.models.purchase_unit_request import PurchaseUnitRequest
from paypalserversdk.models.payment_source import PaymentSource
from paypalserversdk.models.card_request import CardRequest
from paypalserversdk.models.card_attributes import CardAttributes
from paypalserversdk.models.card_verification import CardVerification
from paypalserversdk.models.orders_card_verification_method import (
    OrdersCardVerificationMethod,
)
from paypalserversdk.models.item import Item
from paypalserversdk.models.item_category import ItemCategory
from paypalserversdk.models.payment_source import PaymentSource
from paypalserversdk.models.paypal_wallet import PaypalWallet
from paypalserversdk.models.paypal_wallet_experience_context import (
    PaypalWalletExperienceContext,
)
from paypalserversdk.models.shipping_preference import ShippingPreference
from paypalserversdk.models.paypal_experience_landing_page import (
    PaypalExperienceLandingPage,
)
from paypalserversdk.models.paypal_experience_user_action import (
    PaypalExperienceUserAction,
)
from paypalserversdk.exceptions.error_exception import ErrorException
from paypalserversdk.api_helper import ApiHelper

from furpocalypse_registration import payments
from furpocalypse_registration.models import PaymentWebhookNotification
from furpocalypse_registration.views import common
from furpocalypse_registration.models import Order

logger = logging.getLogger(__name__)

def get_paypal_client():
    """Lazy initialization of PayPal client to avoid issues during module import"""
    from django.conf import settings
    
    # Skip PayPal client creation during tests to prevent network calls and hanging
    if settings.TESTING if hasattr(settings, 'TESTING') else False:
        return None
    
    # Also check for Django test runner
    import sys
    if 'test' in sys.argv:
        return None
    
    client_id = os.getenv("PAYPAL_CLIENT_ID")
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        raise ValueError("PayPal credentials not configured. Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET environment variables.")
    
    # TODO: PayPal Integration - Add PayPal environment configuration
    # Currently hardcoded to use default environment. Should support sandbox/production
    # configuration through Django settings or environment variables.
    # Use Environment.SANDBOX or Environment.PRODUCTION parameter.
    # References:
    # - PayPal SDK Environment Config: https://github.com/paypal/PayPal-server-sdk-python
    # - PayPal Environment Class: from paypalserversdk.models.environment import Environment
    # Related files: settings.py (PayPal configuration)
    return PaypalServersdkClient(
        client_credentials_auth_credentials=ClientCredentialsAuthCredentials(
            o_auth_client_id=client_id,
            o_auth_client_secret=client_secret,
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

def get_orders_controller():
    """Get PayPal orders controller"""
    client = get_paypal_client()
    return client.orders if client else None

def get_payments_controller():
    """Get PayPal payments controller"""
    client = get_paypal_client()
    return client.payments if client else None


@require_POST
@csrf_exempt
def paypal_create_order(request):
    # TODO: PayPal Integration - Integrate with Django Order model (Phase 1 - Critical)
    # DECISION: Online-only checkout integration
    # Current implementation uses hardcoded cart data. Needs to:
    # 1. Extract cart information from Django session or request
    # 2. Calculate actual order totals from cart items (online orders only)
    # 3. Create proper itemized breakdown for PayPal
    # 4. Store PayPal order ID in Django Order model (replace Square structure)
    # 5. Handle billing address requirements for online checkout
    # 6. Apply discounts and donations
    # References:
    # - views/ordering.py (get_total function, checkout flow)
    # - models.py (Order, Cart, OrderItem models)
    try:
        request_body = json.loads(request.body)
    except (ValueError, json.JSONDecodeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    
    # use the cart information passed from the front-end to calculate the order amount detals
    cart = request_body.get("cart", {})
    cart = {
        "currency_code": "USD",
        "order_total": "100",
        "items": [
            {
                "name": "T-Shirt",
                "unit_amount": "100",
                "quantity": "1",
                "description": "Super Fresh Shirt",
                "sku": "sku01",
                "category": "PHYSICAL_GOODS",
            }
        ],
    }
    try:
        orders_controller = get_orders_controller()
        if not orders_controller:
            return JsonResponse({"error": "PayPal client not available"}, status=503)
        
        # TODO: PayPal Integration - Add reference_id and custom_id
        # PayPal orders should include reference_id (our order reference) and
        # custom_id for tracking and webhook processing.
        # References:
        # - PayPal Order Reference: https://developer.paypal.com/docs/api/orders/v2/#definition-purchase_unit_request
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
                                    name="T-Shirt",
                                    unit_amount=Money(currency_code="USD", value="100"),
                                    quantity="1",
                                    description="Super Fresh Shirt",
                                    sku="sku01",
                                    category=ItemCategory.PHYSICAL_GOODS,
                                )
                            ],
                        )
                    ],
                )
            }
        )

        return HttpResponse(
            ApiHelper.json_serialize(order.body), 
            status=200, 
            content_type="application/json"
        )
    except ErrorException as e:
        logger.error(f"PayPal create order error: {e}")
        return JsonResponse({"error": "Order creation failed"}, status=400)


"""
Capture payment for the created order to complete the transaction.

@see https://developer.paypal.com/docs/api/orders/v2/#orders_capture
"""
@require_POST
@csrf_exempt
def paypal_capture_order(request, order_id):
    # TODO: PayPal Integration - Integrate with Django Order processing (Phase 1 - Critical)
    # DECISION: Online-only order processing, complete Square replacement
    # Current implementation only captures PayPal order but doesn't:
    # 1. Update Django Order model with capture data (replace Square structure)
    # 2. Process order items and create badges for online orders
    # 3. Send confirmation emails with PayPal transaction data
    # 4. Handle payment failures appropriately
    # 5. Update order status based on PayPal response
    # References:
    # - views/ordering.py (checkout function - needs PayPal integration)
    # - payments.py (charge_payment function - needs complete rewrite)
    # - models.py (Order model)
    try:
        orders_controller = get_orders_controller()
        if not orders_controller:
            return JsonResponse({"error": "PayPal client not available"}, status=503)
        
        order = orders_controller.capture_order(
            {"id": order_id, "prefer": "return=representation"}
        )
        return HttpResponse(
            ApiHelper.json_serialize(order.body), 
            status=200, 
            content_type="application/json"
        )
    except ErrorException as e:
        logger.error(f"PayPal capture order error: {e}")
        return JsonResponse({"error": "Payment capture failed"}, status=400)


# TODO: PayPal Integration - Implement PayPal webhook endpoints (Phase 1-2)
# DECISION: Online-only payment webhooks, complete Square removal
# PayPal webhooks are essential for handling asynchronous online payment updates.
# Required webhook endpoints for online payments:
# 1. PAYMENT.CAPTURE.COMPLETED - Online payment successfully captured (Phase 1)
# 2. PAYMENT.CAPTURE.DENIED - Online payment was denied (Phase 1)  
# 3. PAYMENT.CAPTURE.REFUNDED - Online payment was refunded (Phase 1)
# 4. CUSTOMER.DISPUTE.CREATED - Dispute initiated (Phase 2)
# 5. CUSTOMER.DISPUTE.RESOLVED - Dispute resolved (Phase 2)
# Implementation: Create @csrf_exempt webhook endpoint that validates PayPal signatures
# References:
# - PayPal Webhook Events: https://developer.paypal.com/docs/api/webhooks/v1/#webhooks_post
# - PayPal Event Types: https://developer.paypal.com/docs/integration-guides/webhooks/
# - PayPal Webhook Verification: https://developer.paypal.com/docs/api/webhooks/v1/#verify-webhook-signature
# Related files: payments.py (webhook processing functions), urls.py (webhook routes)


@require_POST
@csrf_exempt
def paypal_webhook(request):
    """
    Handle PayPal webhook notifications.
    
    PayPal sends webhook notifications for various events including:
    - PAYMENT.CAPTURE.COMPLETED
    - PAYMENT.CAPTURE.DENIED
    - PAYMENT.CAPTURE.REFUNDED
    - CUSTOMER.DISPUTE.CREATED
    - CUSTOMER.DISPUTE.RESOLVED
    """
    try:
        request_body = json.loads(request.body)
    except (ValueError, json.JSONDecodeError):
        return common.abort(400, "Unable to decode JSON")

    # PayPal webhook structure uses 'id' field for event ID
    if "id" not in request_body:
        return common.abort(400, "Missing event id")

    event_id = request_body["id"]
    event_type = request_body.get("event_type")

    # Check to see if webhook was already stored
    existing = PaymentWebhookNotification.objects.filter(event_id=event_id)
    if existing.count() > 0:
        return common.abort(409, f"Conflict: event_id {event_id} already exists")

    # TODO: PayPal Integration - Implement PayPal webhook signature verification
    # PayPal webhooks should be verified for authenticity to prevent fraudulent requests.
    # PayPal provides webhook signature verification similar to Square.
    # References:
    # - PayPal Webhook Verification: https://developer.paypal.com/docs/api/webhooks/v1/#verify-webhook-signature
    # - PayPal SDK Webhook Verification: https://github.com/paypal/PayPal-server-sdk-python
    # Related files: models.py (PaymentWebhookNotification)
    
    # Store the webhook notification
    notification = PaymentWebhookNotification(
        event_id=event_id,
        event_type=event_type,
        body=request_body,
        headers=dict(request.headers),
    )
    try:
        notification.save()
    except Exception as e:
        logger.error("Conflict: event_id already exists:")
        logger.error(e)
        return common.abort(409, str(e))

    # Process the webhook
    paypal_process_webhook(notification)

    return common.success(200)


def paypal_process_webhook(notification):
    """
    Process PayPal webhook notifications.
    
    Maps PayPal event types to appropriate processing functions.
    """
    result = False
    event_type = notification.event_type
    
    if event_type == "PAYMENT.CAPTURE.COMPLETED":
        result = process_paypal_capture_completed(notification)
    elif event_type == "PAYMENT.CAPTURE.DENIED":
        result = process_paypal_capture_denied(notification)
    elif event_type == "PAYMENT.CAPTURE.REFUNDED":
        result = process_paypal_capture_refunded(notification)
    elif event_type == "CUSTOMER.DISPUTE.CREATED":
        result = process_paypal_dispute_created(notification)
    elif event_type == "CUSTOMER.DISPUTE.RESOLVED":
        result = process_paypal_dispute_resolved(notification)
    else:
        logger.info(f"Unhandled PayPal webhook event type: {event_type}")
        result = True  # Mark as processed even if we don't handle it

    notification.processed = result
    notification.save()


def process_paypal_capture_completed(notification):
    """Process PAYMENT.CAPTURE.COMPLETED webhook."""
    try:
        capture_data = notification.body["resource"]
        capture_id = capture_data["id"]
        
        # Find order by PayPal capture ID
        try:
            order = Order.objects.get(
                apiData__purchase_units__0__payments__captures__0__id=capture_id
            )
        except Order.DoesNotExist:
            logger.warning(f"No order found for PayPal capture ID: {capture_id}")
            return False
        
        # Update order status
        order.status = Order.COMPLETED
        order.save()
        
        logger.info(f"PayPal capture completed for order {order.reference}")
        return True
        
    except Exception as e:
        logger.exception(f"Error processing PayPal capture completed: {e}")
        return False


def process_paypal_capture_denied(notification):
    """Process PAYMENT.CAPTURE.DENIED webhook."""
    try:
        capture_data = notification.body["resource"]
        capture_id = capture_data["id"]
        
        # Find order by PayPal capture ID
        try:
            order = Order.objects.get(
                apiData__purchase_units__0__payments__captures__0__id=capture_id
            )
        except Order.DoesNotExist:
            logger.warning(f"No order found for PayPal capture ID: {capture_id}")
            return False
        
        # Update order status
        order.status = Order.FAILED
        order.save()
        
        logger.info(f"PayPal capture denied for order {order.reference}")
        return True
        
    except Exception as e:
        logger.exception(f"Error processing PayPal capture denied: {e}")
        return False


def process_paypal_capture_refunded(notification):
    """Process PAYMENT.CAPTURE.REFUNDED webhook."""
    try:
        refund_data = notification.body["resource"]
        refund_id = refund_data["id"]
        
        # Find order by searching for refund in apiData
        try:
            order = Order.objects.get(apiData__refunds__contains=[{"id": refund_id}])
        except Order.DoesNotExist:
            logger.warning(f"No order found for PayPal refund ID: {refund_id}")
            return False
        
        # Update refund status in order
        refunds = order.apiData.get("refunds", [])
        for refund in refunds:
            if refund.get("id") == refund_id:
                refund.update(refund_data)
                break
        
        order.apiData["refunds"] = refunds
        
        # Update order status based on refund status
        refund_status = refund_data.get("status")
        if refund_status == "COMPLETED":
            order.status = Order.REFUNDED
        elif refund_status == "PENDING":
            order.status = Order.REFUND_PENDING
        
        order.save()
        
        logger.info(f"PayPal refund updated for order {order.reference}")
        return True
        
    except Exception as e:
        logger.exception(f"Error processing PayPal refund: {e}")
        return False


def process_paypal_dispute_created(notification):
    """Process CUSTOMER.DISPUTE.CREATED webhook."""
    # TODO: PayPal Integration - Implement PayPal dispute processing (Phase 2)
    # DECISION: Replace Square dispute handling with PayPal dispute processing
    # PayPal disputes use different statuses: OPEN, WAITING_FOR_BUYER_RESPONSE, etc.
    # This function will be rewritten for PayPal dispute webhook events.
    # Priority: Phase 2 (after core payment processing is restored)
    # References:
    # - PayPal Disputes API: https://developer.paypal.com/docs/api/customer-disputes/v1/
    # - PayPal Dispute Webhooks: https://developer.paypal.com/docs/integration-guides/webhooks/
    # Related files: models.py (Order.DISPUTE_STATUS_MAP), emails.py (chargeback notifications)
    raise NotImplementedError("PayPal dispute processing not yet implemented - Phase 2 feature")


def process_paypal_dispute_resolved(notification):
    """Process CUSTOMER.DISPUTE.RESOLVED webhook."""
    # TODO: PayPal Integration - Implement PayPal dispute processing (Phase 2)
    raise NotImplementedError("PayPal dispute processing not yet implemented - Phase 2 feature")
