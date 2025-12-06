import base64
import json
import logging
import time
import uuid
from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.contrib.messages import get_messages
from django.core.signing import TimestampSigner
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.csrf import csrf_exempt

from furpocalypse_registration import admin, mqtt, payments
from furpocalypse_registration.admin import TWOPLACES
from furpocalypse_registration.models import (
    Badge,
    Cashdrawer,
    Discount,
    Event,
    Firebase,
    Order,
    OrderItem,
)
from furpocalypse_registration.mqtt import send_mqtt_message
from furpocalypse_registration.pushy import PushyAPI, PushyError
from furpocalypse_registration.views.attendee import get_attendee_age
from furpocalypse_registration.views.common import logger
from furpocalypse_registration.views.ordering import (
    get_discount_total,
    get_order_item_option_total,
)

class register:
    def business_member(request) -> httpResponse:
        """
        Use JWT with additional packed information to pre-populate fields, and force them to 
        use those values.  This is so that links sent out to each dealer are unique, and even
        if exposed the worst that can happen is that someone else pays for that dealer.
        """

        pass

    def convention_staff(request) -> httpResponse:
        pass

    def guest(request) -> httpResponse:
        return render(request, "new_registration/baase.html")
