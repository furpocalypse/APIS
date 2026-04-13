from django.core.management.base import BaseCommand

from registration.models import (
    AttendeeOptions,
    Event,
    PriceLevelOption,
    ShirtSizes,
)


class Command(BaseCommand):
    help = "Generates a merchandise (order items) report"

    def prompt_int(self, prompt, silent=False):
        selection = input(prompt)
        try:
            return int(selection)
        except ValueError:
            if not silent:
                print("Invalid selection!")
            return None

    def handle(self, *args, **options):
        default_event = Event.objects.get(default=True)
        SHIRT_SIZES = {str(shirt.id): shirt.name for shirt in ShirtSizes.objects.filter()}
        for idx, event in enumerate(Event.objects.filter()):
            print(f"{idx + 1} - {event}")
        selection = self.prompt_int(
            f"Enter index of event to report on [{default_event.name}] > ",
            True,
        )
        event = default_event if selection is None else Event.objects.get(id=selection)
        options = PriceLevelOption.objects.filter()
        for oc, option in enumerate(options):
            print(f"{oc} - {option}")
        selection = self.prompt_int("Enter index of item to report on > ")
        if selection is None:
            return
        selected_options = AttendeeOptions.objects.filter(
            option=options[selection], orderItem__badge__event=event
        )
        bins = {}
        levels = {}
        print(
            f"{selected_options.count()} orders with {options[selection]} option selected for {event}"
        )
        for attendee_option in selected_options:
            attendee_option.orderItem.badge  # noqa: B018
            try:
                levels[str(attendee_option.orderItem.priceLevel)] += 1
            except KeyError:
                levels[str(attendee_option.orderItem.priceLevel)] = 1

            if attendee_option.option.optionExtraType == "ShirtSizes":
                assert str(attendee_option.optionValue) in list(SHIRT_SIZES.keys()), (
                    f"Invalid response in AttendeeOption(id={attendee_option.id})"
                )
                try:
                    bins[SHIRT_SIZES[str(attendee_option.optionValue)]] += 1
                except KeyError:
                    bins[SHIRT_SIZES[str(attendee_option.optionValue)]] = 1
            else:
                try:
                    bins[str(attendee_option.optionValue)] += 1
                except KeyError:
                    bins[str(attendee_option.optionValue)] = 1
        for k, v in bins.items():
            print(f"{k}, {v}")
        print("Levels:")
        for k, v in levels.items():
            print(f"{k}, {v}")
