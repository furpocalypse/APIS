import json
from datetime import date

from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from registration.models import Badge, Event, PriceLevel


def format_price_level_list(levels):
    data = [
        {
            "name": level.name,
            "id": level.id,
            "base_price": level.basePrice.__str__(),
            "description": level.description,
            "accompanied": level.accompanied,
            "is_minor": level.isMinor,
            "capacity": level.get_capacity_status(),
            "options": [
                {
                    "name": option.optionName,
                    "value": option.optionPrice,
                    "id": option.id,
                    "required": option.required,
                    "active": option.active,
                    "type": option.optionExtraType,
                    "image": option.getOptionImage(),
                    "description": option.description,
                    "list": option.getList(),
                }
                for option in level.priceLevelOptions.filter(public=True)
                .order_by("rank", "optionPrice")
                .all()
            ],
        }
        for level in levels
    ]
    return data


@csrf_exempt
def get_price_levels(request):
    current_event = Event.objects.get(default=True)
    if request.method == "POST":
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            response = {"status": "error", "message": "Invalid JSON data"}
            return JsonResponse(response, status=400)

        origLevel = None
        dob = None
        try:
            if data.get("badge_id"):
                badge = Badge.objects.get(id=data.get("badge_id"))
                if badge and badge.attendee:
                    origLevel = badge.effectiveLevel()
                    dob = badge.attendee.birthdate

            if not dob:
                dob = date(int(data.get("year")), int(data.get("month")), int(data.get("day")))
            form_type = data.get("form_type")
        except Exception:
            response = {"status": "error", "message": "Invalid birthdate, form_type, or badge_id"}
            return JsonResponse(response, status=400)

        age_at_event = (
            current_event.eventStart.year
            - dob.year
            - (
                (current_event.eventStart.month, current_event.eventStart.day)
                < (dob.month, dob.day)
            )
        )

        now = timezone.now()
        age_appropriate_levels = PriceLevel.objects.filter(
            Q(public=True)
            & Q(startDate__lte=now)
            & Q(endDate__gte=now)
            & Q(min_age__lte=age_at_event)
            & (Q(max_age__gte=age_at_event) | Q(max_age__isnull=True))
        ).order_by("basePrice")

        match form_type:
            case "staff":
                available_levels = age_appropriate_levels.filter(available_to_staff=True)
            case "marketplace":
                available_levels = age_appropriate_levels.filter(available_to_marketplace=True)
            case _:
                # probably tighten this up at some point
                available_levels = age_appropriate_levels.filter(available_to_attendee=True)

        if isinstance(origLevel, PriceLevel):
            available_levels = available_levels.filter(Q(basePrice__gt=origLevel.basePrice))

        data = format_price_level_list(available_levels)

        return JsonResponse(data, safe=False)

    else:
        response = {"status": "error", "message": "Only POST requests are allowed"}
    return JsonResponse(response, status=400)
