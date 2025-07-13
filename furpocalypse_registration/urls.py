from django.urls import re_path
from django.contrib.auth.views import LogoutView

import furpocalypse_registration.views.attendee
import furpocalypse_registration.views.cart
import furpocalypse_registration.views.common
import furpocalypse_registration.views.dealers
import furpocalypse_registration.views.onsite
import furpocalypse_registration.views.onsite_admin
import furpocalypse_registration.views.ordering
import furpocalypse_registration.views.printing
import furpocalypse_registration.views.staff
import furpocalypse_registration.views.upgrade
import furpocalypse_registration.views.webhooks

app_name = "furpocalypse_registration"


def trigger_error(request):
    division_by_zero = 1 / 0
    return division_by_zero


urlpatterns = [
    re_path(r"^sentry-debug/", trigger_error),
    re_path(r"^$", furpocalypse_registration.views.common.index, name="index"),
    re_path(r"^logout/$", LogoutView.as_view(), name="logout"),
    re_path(
        r"^upgrade/lookup/?$",
        furpocalypse_registration.views.upgrade.find_upgrade,
        name="find_upgrade",
    ),
    re_path(
        r"^upgrade/info/?$",
        furpocalypse_registration.views.upgrade.info_upgrade,
        name="info_upgrade",
    ),
    re_path(
        r"^upgrade/add/?$",
        furpocalypse_registration.views.upgrade.add_upgrade,
        name="add_upgrade",
    ),
    # re_path(
    #     r"^upgrade/invoice/?$",
    #     furpocalypse_registration.views.upgrade.invoice_upgrade,
    #     name="invoice_upgrade",
    # ),
    # re_path(
    #     r"^upgrade/checkout/?$",
    #     furpocalypse_registration.views.upgrade.checkout_upgrade,
    #     name="checkout_upgrade",
    # ),
    re_path(
        r"^upgrade/done/?$",
        furpocalypse_registration.views.upgrade.done_upgrade,
        name="done_upgrade",
    ),
    re_path(
        r"^upgrade/(?P<guid>\w+)/?$",
        furpocalypse_registration.views.upgrade.upgrade,
        name="upgrade",
    ),
    re_path(
        r"^staff/done/?$",
        furpocalypse_registration.views.staff.staff_done,
        name="staff_done",
    ),
    re_path(
        r"^staff/lookup/?$",
        furpocalypse_registration.views.staff.find_staff,
        name="find_staff",
    ),
    re_path(
        r"^staff/info/?$",
        furpocalypse_registration.views.staff.info_staff,
        name="info_staff",
    ),
    re_path(
        r"^staff/add/?$",
        furpocalypse_registration.views.staff.add_staff,
        name="add_staff",
    ),
    re_path(
        r"^staff/(?P<guid>\w+)/?$",
        furpocalypse_registration.views.staff.staff_index,
        name="staff",
    ),
    re_path(
        r"^newstaff/done/?$",
        furpocalypse_registration.views.staff.staff_done,
        name="doneNewStaff",
    ),
    re_path(
        r"^newstaff/lookup/?$",
        furpocalypse_registration.views.staff.find_new_staff,
        name="find_new_staff",
    ),
    re_path(
        r"^newstaff/info/?$",
        furpocalypse_registration.views.staff.info_new_staff,
        name="info_new_staff",
    ),
    re_path(
        r"^newstaff/add/?$",
        furpocalypse_registration.views.staff.add_new_staff,
        name="add_new_staff",
    ),
    re_path(
        r"^newstaff/(?P<guid>\w+)/?$",
        furpocalypse_registration.views.staff.new_staff,
        name="new_staff",
    ),
    re_path(r"^dealer/?$", furpocalypse_registration.views.dealers.new_dealer, name="new_dealer"),
    re_path(
        r"^dealer/addNew/?$",
        furpocalypse_registration.views.dealers.addNewDealer,
        name="addNewDealer",
    ),
    re_path(
        r"^dealer/done/?$",
        furpocalypse_registration.views.dealers.done_dealer,
        name="done_dealer",
    ),
    re_path(
        r"^dealer/thanks/?$",
        furpocalypse_registration.views.dealers.thanks_dealer,
        name="thanks_dealer",
    ),
    re_path(
        r"^dealer/lookup/?$",
        furpocalypse_registration.views.dealers.find_dealer,
        name="find_dealer",
    ),
    re_path(
        r"^dealer/add/?$",
        furpocalypse_registration.views.dealers.add_dealer,
        name="add_dealer",
    ),
    re_path(
        r"^dealer/info/?$",
        furpocalypse_registration.views.dealers.info_dealer,
        name="info_dealer",
    ),
    # re_path(
    #     r"^dealer/invoice/?$",
    #     furpocalypse_registration.views.dealers.invoice_dealer,
    #     name="invoice_dealer",
    # ),
    # re_path(
    #     r"^dealer/checkout/?$",
    #     furpocalypse_registration.views.dealers.checkout_dealer,
    #     name="checkout_dealer",
    # ),
    re_path(
        r"^dealer/(?P<guid>\w+)/?$",
        furpocalypse_registration.views.dealers.dealers,
        name="dealers",
    ),
    re_path(
        r"^dealer/(?P<guid>\w+)/assistants/?$",
        furpocalypse_registration.views.dealers.find_dealer_to_add_assistant,
        name="find_dealer_to_add_assistant",
    ),
    re_path(
        r"^dealer/assistants/lookup/?$",
        furpocalypse_registration.views.dealers.find_dealer_to_add_assistant_post,
        name="find_dealer_to_add_assistant_post",
    ),
    re_path(
        r"^dealer/assistants/add/?$",
        furpocalypse_registration.views.dealers.add_assistants,
        name="add_assistants",
    ),
    # re_path(
    #     r"^dealer/assistants/checkout/?$",
    #     furpocalypse_registration.views.dealers.add_assistants_checkout,
    #     name="add_assistants_checkout",
    # ),
    re_path(
        r"^dealerassistant/(?P<guid>\w+)/?$",
        furpocalypse_registration.views.dealers.dealer_asst,
        name="dealer_asst",
    ),
    re_path(
        r"^dealerassistant/add/find/?$",
        furpocalypse_registration.views.dealers.find_asst_dealer,
        name="find_asst_dealer",
    ),
    re_path(
        r"^dealerassistant/add/done/?$",
        furpocalypse_registration.views.dealers.done_asst_dealer,
        name="done_asst_dealer",
    ),
    re_path(r"^onsite/?$", furpocalypse_registration.views.onsite.onsite, name="onsite"),
    re_path(
        r"^onsite/cart/?$",
        furpocalypse_registration.views.onsite.onsite_cart,
        name="onsite_cart",
    ),
    re_path(
        r"^onsite/done/?$",
        furpocalypse_registration.views.onsite.onsite_done,
        name="onsite_done",
    ),
    re_path(
        r"^onsite/admin/?$",
        furpocalypse_registration.views.onsite_admin.onsite_admin,
        name="onsite_admin",
    ),
    re_path(
        r"^onsite/admin/search/?$",
        furpocalypse_registration.views.onsite_admin.onsite_admin_search,
        name="onsite_admin_search",
    ),
    re_path(
        r"^onsite/admin/cart/?$",
        furpocalypse_registration.views.onsite_admin.onsite_admin_cart,
        name="onsite_admin_cart",
    ),
    re_path(
        r"^onsite/admin/cart/add/?$",
        furpocalypse_registration.views.onsite_admin.onsite_add_to_cart,
        name="onsite_add_to_cart",
    ),
    re_path(
        r"^onsite/admin/cart/remove/?$",
        furpocalypse_registration.views.onsite_admin.onsite_remove_from_cart,
        name="onsite_remove_from_cart",
    ),
    re_path(
        r"^onsite/admin/open/?$",
        furpocalypse_registration.views.onsite_admin.open_terminal,
        name="open_terminal",
    ),
    re_path(
        r"^onsite/admin/close/?$",
        furpocalypse_registration.views.onsite_admin.close_terminal,
        name="close_terminal",
    ),
    re_path(
        r"^onsite/admin/ready/?$",
        furpocalypse_registration.views.onsite_admin.ready_terminal,
        name="ready_terminal",
    ),
    re_path(
        r"^onsite/admin/payment/?$",
        furpocalypse_registration.views.onsite_admin.enable_payment,
        name="enable_payment",
    ),
    re_path(
        r"^onsite/admin/clear/?$",
        furpocalypse_registration.views.onsite_admin.onsite_admin_clear_cart,
        name="onsite_admin_clear_cart",
    ),
    re_path(
        r"^onsite/admin/badge/assign/?$",
        furpocalypse_registration.views.onsite_admin.assign_badge_number,
        name="assign_badge_number",
    ),
    re_path(
        r"^onsite/admin/badge/print/?$",
        furpocalypse_registration.views.onsite_admin.onsite_print_badges,
        name="onsite_print_badges",
    ),
    # TODO: PayPal Integration - Remove Square-specific URLs (Phase 3 - Cleanup)
    # DECISION: Complete Square removal, online-only payments
    # The following Square-related URLs will be removed entirely:
    # - complete_square_transaction (POS integration - not needed)
    # - square_webhook (commented out - remove completely)
    # References:
    # - views/onsite_admin.py (complete_square_transaction function - remove)
    # - views/webhooks.py (square_webhook function - remove)
    re_path(
        r"^onsite/square/complete/?$",
        furpocalypse_registration.views.onsite_admin.complete_square_transaction,
        name="complete_square_transaction",
    ),
    re_path(
        r"^onsite/cash/complete/?$",
        furpocalypse_registration.views.onsite_admin.complete_cash_transaction,
        name="complete_cash_transaction",
    ),
    re_path(
        r"^onsite/cashdrawer/status/?$",
        furpocalypse_registration.views.onsite_admin.drawer_status,
        name="drawer_status",
    ),
    re_path(
        r"^onsite/cashdrawer/open/?$",
        furpocalypse_registration.views.onsite_admin.open_drawer,
        name="open_drawer",
    ),
    re_path(
        r"^onsite/cashdrawer/deposit/?$",
        furpocalypse_registration.views.onsite_admin.cash_deposit,
        name="cash_deposit",
    ),
    re_path(
        r"^onsite/cashdrawer/safedrop/?$",
        furpocalypse_registration.views.onsite_admin.safe_drop,
        name="safe_drop",
    ),
    re_path(
        r"^onsite/cashdrawer/pickup/?$",
        furpocalypse_registration.views.onsite_admin.cash_pickup,
        name="cash_pickup",
    ),
    re_path(
        r"^onsite/cashdrawer/close/?$",
        furpocalypse_registration.views.onsite_admin.close_drawer,
        name="close_drawer",
    ),
    re_path(
        r"^onsite/cashdrawer/no_sale/?$",
        furpocalypse_registration.views.onsite_admin.no_sale,
        name="no_sale",
    ),
    re_path(
        r"^onsite/admin/discount/create/?$",
        furpocalypse_registration.views.onsite_admin.create_discount,
        name="onsite_create_discount",
    ),
    re_path(r"^cart/?$", furpocalypse_registration.views.cart.get_cart, name="cart"),
    re_path(r"^cart/add/?$", furpocalypse_registration.views.cart.add_to_cart, name="add_to_cart"),
    re_path(
        r"^cart/remove/?$",
        furpocalypse_registration.views.cart.remove_from_cart,
        name="remove_from_cart",
    ),
    re_path(
        r"^cart/abandon/?$",
        furpocalypse_registration.views.ordering.cancel_order,
        name="cancel_order",
    ),
    re_path(
        r"^cart/discount/?$",
        furpocalypse_registration.views.ordering.apply_discount,
        name="discount",
    ),
    re_path(
        r"^cart/checkout/?$",
        furpocalypse_registration.views.ordering.checkout,
        name="checkout",
    ),
    re_path(r"^cart/done/?$", furpocalypse_registration.views.cart.cart_done, name="done"),
    re_path(r"^events/?$", furpocalypse_registration.views.common.get_events, name="events"),
    re_path(
        r"^departments/?$",
        furpocalypse_registration.views.common.get_departments,
        name="departments",
    ),
    re_path(
        r"^alldepartments/?$",
        furpocalypse_registration.views.common.get_all_departments,
        name="alldepartments",
    ),
    re_path(
        r"^pricelevels/?$",
        furpocalypse_registration.views.attendee.get_price_levels,
        name="pricelevels",
    ),
    re_path(
        r"^adultpricelevels/?$",
        furpocalypse_registration.views.attendee.get_adult_price_levels,
        name="adultpricelevels",
    ),
    re_path(
        r"^minorpricelevels/?$",
        furpocalypse_registration.views.attendee.get_minor_price_levels,
        name="minorpricelevels",
    ),
    re_path(
        r"^accompaniedpricelevels/?$",
        furpocalypse_registration.views.attendee.get_accompanied_price_levels,
        name="accompaniedpricelevels",
    ),
    re_path(
        r"^freepricelevels/?$",
        furpocalypse_registration.views.attendee.get_free_price_levels,
        name="freepricelevels",
    ),
    re_path(
        r"^shirts/?$",
        furpocalypse_registration.views.common.get_shirt_sizes,
        name="shirtsizes",
    ),
    re_path(
        r"^tables/?$",
        furpocalypse_registration.views.dealers.getTableSizes,
        name="tablesizes",
    ),
    re_path(
        r"^addresses/?$",
        furpocalypse_registration.views.common.get_session_addresses,
        name="addresses",
    ),
    re_path(
        r"^utility/badges?$",
        furpocalypse_registration.views.common.basicBadges,
        name="basicBadges",
    ),
    re_path(
        r"^utility/vips?$",
        furpocalypse_registration.views.common.vipBadges,
        name="vipBadges",
    ),
    re_path(r"^flush/?$", furpocalypse_registration.views.common.flush, name="flush"),
    re_path(r"^pdf/?$", furpocalypse_registration.views.printing.servePDF, name="pdf"),
    re_path(r"^print/?$", furpocalypse_registration.views.printing.printNametag, name="print"),
    re_path(
        r"^firebase/register/?",
        furpocalypse_registration.views.onsite_admin.firebase_register,
        name="firebase_register",
    ),
    re_path(
        r"^firebase/lookup/?",
        furpocalypse_registration.views.onsite_admin.firebase_lookup,
        name="firebase_lookup",
    ),
    # TODO: PayPal Integration - Remove Square webhook URL (Phase 3 - Cleanup)
    # DECISION: Complete Square removal
    # This Square webhook URL is commented out and will be completely removed.
    # re_path(
    #     r"webhook/square/v2",
    #     furpocalypse_registration.views.webhooks.square_webhook,
    #     name="square_webhook",
    # ),
    # TODO: PayPal Integration - Add comprehensive PayPal webhook endpoints (Phase 1-2)
    # DECISION: Online-only PayPal webhooks, complete Square replacement
    # Current PayPal webhook endpoints are basic and only handle order creation/capture.
    # Need to add endpoints for online payment processing:
    # 1. PAYMENT.CAPTURE.COMPLETED - Online payment successfully captured (Phase 1)
    # 2. PAYMENT.CAPTURE.DENIED - Online payment was denied (Phase 1)
    # 3. PAYMENT.CAPTURE.REFUNDED - Online payment was refunded (Phase 1)
    # 4. CUSTOMER.DISPUTE.CREATED - Dispute initiated (Phase 2)
    # 5. CUSTOMER.DISPUTE.RESOLVED - Dispute resolved (Phase 2)
    # 6. Generic PayPal webhook endpoint with signature verification (Phase 1)
    # References:
    # - PayPal Webhook Events: https://developer.paypal.com/docs/api/webhooks/v1/
    # - views/webhooks.py (webhook endpoint implementations needed)
    re_path(
        r"webhook/paypal/v1/create-order",
        furpocalypse_registration.views.webhooks.paypal_create_order,
        name="paypal_create_order",
    ),
    re_path(
        r"webhook/paypal/v1/capture-order",
        furpocalypse_registration.views.webhooks.paypal_capture_order,
        name="paypal_capture_order",
    ),
]
