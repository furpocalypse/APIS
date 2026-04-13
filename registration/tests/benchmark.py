import asyncio
import json
import logging
import random
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Optional

import httpx
from django.test import LiveServerTestCase, tag
from django.utils import timezone

from registration.models import Event, PriceLevel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class TierConfig:
    """Defines a registration tier and how many simulated users target it."""

    name: str
    price: Decimal
    max_capacity: Optional[int]  # None = unlimited
    num_users: int


@dataclass
class ThinkTimes:
    """Random delay ranges (min, max) in seconds between each phase."""

    after_landing: tuple[float, float] = (2.0, 5.0)
    after_tier_select: tuple[float, float] = (15.0, 45.0)
    after_add_cart: tuple[float, float] = (5.0, 15.0)
    after_view_cart: tuple[float, float] = (5.0, 20.0)


@dataclass
class ScenarioConfig:
    """Full benchmark scenario definition."""

    name: str
    tiers: list[TierConfig]
    ramp_up_seconds: float = 10.0
    think_times: ThinkTimes = field(default_factory=ThinkTimes)
    retry_delay: tuple[float, float] = (5.0, 15.0)
    max_retries: int = 10


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class StepTiming:
    endpoint: str
    latency_ms: float
    status_code: int


@dataclass
class UserResult:
    user_id: int
    tier_name: str
    outcome: str = "error"  # success | sold_out | error | retry_exhausted
    timings: list[StepTiming] = field(default_factory=list)
    checkout_retries: int = 0
    total_duration: float = 0.0
    error_message: str = ""


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (k - f) * (s[c] - s[f])


class BenchmarkReport:
    def __init__(
        self,
        scenario: ScenarioConfig,
        results: list[UserResult],
        wall_clock_time: float,
    ):
        self.scenario = scenario
        self.results = results
        self.wall_clock_time = wall_clock_time

    def tier_results(self, tier_name: str) -> list[UserResult]:
        return [r for r in self.results if r.tier_name == tier_name]

    def print_summary(self):
        sep = "=" * 80
        thin = "-" * 80
        out = sys.stdout

        out.write(f"\n{sep}\n")
        out.write(f"Benchmark: {self.scenario.name}\n")
        out.write(f"{sep}\n")

        out.write("Tiers:\n")
        for t in self.scenario.tiers:
            cap = (
                f"{t.max_capacity} slots" if t.max_capacity is not None else "unlimited"
            )
            out.write(f"  {t.name} (${t.price}, {cap}): {t.num_users} users\n")
        out.write(
            f"Ramp-up: {self.scenario.ramp_up_seconds}s | "
            f"Retry delay: {self.scenario.retry_delay[0]}-{self.scenario.retry_delay[1]}s | "
            f"Max retries: {self.scenario.max_retries}\n"
        )
        out.write(f"{thin}\n\n")

        # Overall results
        total = len(self.results)
        by_outcome = Counter(r.outcome for r in self.results)
        out.write("Results:\n")
        out.write(f"  Total users: {total}\n")
        for outcome in ["success", "sold_out", "error", "retry_exhausted"]:
            n = by_outcome.get(outcome, 0)
            pct = (n / total * 100) if total else 0
            out.write(f"  {outcome}: {n} ({pct:.1f}%)\n")
        out.write("\n")

        # Per-tier breakdown
        out.write("Per-tier breakdown:\n")
        for t in self.scenario.tiers:
            tr = self.tier_results(t.name)
            tier_by_outcome = Counter(r.outcome for r in tr)
            out.write(f"  {t.name}:\n")
            for outcome in ["success", "sold_out", "error", "retry_exhausted"]:
                n = tier_by_outcome.get(outcome, 0)
                out.write(f"    {outcome}: {n}/{len(tr)}\n")
        out.write("\n")

        # Latency table
        endpoints = [
            "GET /registration/",
            "POST /pricelevels/",
            "POST /cart/add/",
            "GET /cart/",
            "POST /checkout/",
        ]
        out.write("Latency (ms):\n")
        out.write(f"  {'Endpoint':<25} {'p50':>7} {'p95':>7} {'p99':>7} {'max':>7}\n")
        for ep in endpoints:
            latencies = [
                t.latency_ms
                for r in self.results
                for t in r.timings
                if t.endpoint == ep
            ]
            if latencies:
                out.write(
                    f"  {ep:<25} "
                    f"{_percentile(latencies, 50):>7.0f} "
                    f"{_percentile(latencies, 95):>7.0f} "
                    f"{_percentile(latencies, 99):>7.0f} "
                    f"{max(latencies):>7.0f}\n"
                )
        out.write("\n")

        # Retry distribution
        retry_counts = Counter(r.checkout_retries for r in self.results)
        out.write("Checkout retries:\n")
        for retries in sorted(retry_counts):
            label = f"{retries} retries" if retries != 1 else "1 retry"
            out.write(f"  {label}: {retry_counts[retries]} users\n")
        out.write("\n")

        # Capacity validation
        out.write("Capacity validation:\n")
        all_valid = True
        for t in self.scenario.tiers:
            if t.max_capacity is not None:
                successes = sum(
                    1 for r in self.tier_results(t.name) if r.outcome == "success"
                )
                ok = successes <= t.max_capacity
                mark = "OK" if ok else "OVERSOLD"
                out.write(
                    f"  {t.name}: {successes} registered / {t.max_capacity} max  {mark}\n"
                )
                if not ok:
                    all_valid = False
        if all_valid:
            out.write("  All capacity limits respected.\n")
        out.write("\n")

        out.write(f"Wall clock time: {self.wall_clock_time:.1f}s\n")
        out.write(f"{sep}\n\n")
        out.flush()

        return all_valid


# ---------------------------------------------------------------------------
# User simulator
# ---------------------------------------------------------------------------


class UserSimulator:
    def __init__(
        self,
        user_id: int,
        tier_name: str,
        price_level_id: int,
        event_name: str,
        server_url: str,
        think_times: ThinkTimes,
        retry_delay: tuple[float, float],
        max_retries: int,
    ):
        self.user_id = user_id
        self.tier_name = tier_name
        self.price_level_id = price_level_id
        self.event_name = event_name
        self.server_url = server_url
        self.think_times = think_times
        self.retry_delay = retry_delay
        self.max_retries = max_retries

    async def run(self, start_delay: float) -> UserResult:
        result = UserResult(user_id=self.user_id, tier_name=self.tier_name)
        t_start = time.monotonic()

        await asyncio.sleep(start_delay)

        limits = httpx.Limits(max_connections=1, max_keepalive_connections=1)
        async with httpx.AsyncClient(
            limits=limits,
            timeout=httpx.Timeout(60.0),
            follow_redirects=True,
        ) as client:
            try:
                # Step 1: GET landing page (establishes session + CSRF cookie)
                t0 = time.monotonic()
                resp = await client.get(f"{self.server_url}/registration/")
                result.timings.append(
                    StepTiming(
                        "GET /registration/",
                        (time.monotonic() - t0) * 1000,
                        resp.status_code,
                    )
                )

                csrf_token = client.cookies.get("csrftoken", "")
                csrf_headers = {"X-CSRFToken": csrf_token}

                # Step 2: POST get price levels (csrf_exempt, but header is harmless)
                await asyncio.sleep(random.uniform(*self.think_times.after_landing))
                t0 = time.monotonic()
                resp = await client.post(
                    f"{self.server_url}/registration/pricelevels/",
                    json={"year": 1990, "month": 1, "day": 1, "form_type": "attendee"},
                    headers=csrf_headers,
                )
                result.timings.append(
                    StepTiming(
                        "POST /pricelevels/",
                        (time.monotonic() - t0) * 1000,
                        resp.status_code,
                    )
                )

                # Step 3: POST add to cart
                await asyncio.sleep(random.uniform(*self.think_times.after_tier_select))
                cart_payload = {
                    "attendee": {
                        "firstName": "Bench",
                        "lastName": f"User{self.user_id}",
                        "address1": "123 Benchmark St",
                        "address2": "",
                        "city": "Testville",
                        "state": "PA",
                        "country": "US",
                        "postal": "12345",
                        "phone": "5551234567",
                        "email": f"bench_{self.user_id}@test.invalid",
                        "birthdate": "1990-01-01",
                        "asl": "false",
                        "badgeName": f"BenchUser{self.user_id}",
                        "emailsOk": "false",
                        "volunteer": "false",
                        "volDepts": "",
                        "surveyOk": "false",
                    },
                    "priceLevel": {"id": self.price_level_id, "options": []},
                    "event": self.event_name,
                }

                t0 = time.monotonic()
                resp = await client.post(
                    f"{self.server_url}/registration/cart/add/",
                    json=cart_payload,
                    headers=csrf_headers,
                )
                result.timings.append(
                    StepTiming(
                        "POST /cart/add/",
                        (time.monotonic() - t0) * 1000,
                        resp.status_code,
                    )
                )

                if resp.status_code != 200:
                    result.outcome = "error"
                    result.error_message = (
                        f"add_to_cart {resp.status_code}: {resp.text[:200]}"
                    )
                    result.total_duration = time.monotonic() - t_start
                    return result

                # Step 4: GET cart page
                await asyncio.sleep(random.uniform(*self.think_times.after_add_cart))
                t0 = time.monotonic()
                resp = await client.get(f"{self.server_url}/registration/cart/")
                result.timings.append(
                    StepTiming(
                        "GET /cart/", (time.monotonic() - t0) * 1000, resp.status_code
                    )
                )

                # Step 5: POST checkout (retries on "reserved")
                await asyncio.sleep(random.uniform(*self.think_times.after_view_cart))

                checkout_payload = {
                    "billingData": {
                        "cc_firstname": "Bench",
                        "cc_lastname": f"User{self.user_id}",
                        "address1": "123 Benchmark St",
                        "address2": "",
                        "city": "Testville",
                        "state": "PA",
                        "country": "US",
                        "email": f"bench_{self.user_id}@test.invalid",
                        "source_id": "cnon:card-nonce-ok",
                        "postal": "12345",
                    },
                    "onsite": False,
                    "orgDonation": "0",
                    "charityDonation": "0",
                }

                for attempt in range(self.max_retries + 1):
                    t0 = time.monotonic()
                    resp = await client.post(
                        f"{self.server_url}/registration/cart/checkout/",
                        json=checkout_payload,
                        headers={
                            **csrf_headers,
                            "Idempotency-Key": str(uuid.uuid4()),
                        },
                    )
                    latency = (time.monotonic() - t0) * 1000
                    result.timings.append(
                        StepTiming("POST /checkout/", latency, resp.status_code)
                    )

                    try:
                        data = resp.json()
                    except (json.JSONDecodeError, ValueError):
                        result.outcome = "error"
                        result.error_message = f"Non-JSON response ({resp.status_code})"
                        break

                    if data.get("success"):
                        result.outcome = "success"
                        break

                    reason = data.get("reason", "")
                    if not isinstance(reason, str):
                        reason = json.dumps(reason)

                    reason_lower = reason.lower()

                    if "sold out" in reason_lower and "reserved" not in reason_lower:
                        # Definitively sold out, no point retrying
                        result.outcome = "sold_out"
                        break
                    elif "reserved" in reason_lower:
                        # Capacity temporarily held by pending payments — retry
                        result.checkout_retries += 1
                        if attempt < self.max_retries:
                            await asyncio.sleep(random.uniform(*self.retry_delay))
                            continue
                        else:
                            result.outcome = "retry_exhausted"
                            break
                    elif (
                        "session expired" in reason_lower
                        or "nothing in your cart" in reason_lower
                    ):
                        # Session lost — cannot recover
                        result.outcome = "error"
                        result.error_message = reason
                        break
                    else:
                        result.outcome = "error"
                        result.error_message = reason
                        break

            except Exception as e:
                result.outcome = "error"
                result.error_message = str(e)

        result.total_duration = time.monotonic() - t_start
        return result


# ---------------------------------------------------------------------------
# Base benchmark test case
# ---------------------------------------------------------------------------


@tag("benchmark")
class BaseBenchmark(LiveServerTestCase):
    """
    Subclass this and set `scenario` to a ScenarioConfig instance.

    Run all benchmarks:
        python manage.py test --tag benchmark

    Run one specific benchmark:
        python manage.py test registration.tests.test_benchmarks.SmallCapacityRush
    """

    scenario: ScenarioConfig

    def setUp(self):
        if not hasattr(self, "scenario"):
            self.skipTest("BaseBenchmark is abstract — run a concrete subclass instead")

        now = timezone.now()
        ten_days = timedelta(days=10)

        self.event = Event.objects.create(
            default=True,
            name="Benchmark Event",
            dealerRegStart=now - ten_days,
            dealerRegEnd=now + ten_days,
            staffRegStart=now - ten_days,
            staffRegEnd=now + ten_days,
            attendeeRegStart=now - ten_days,
            attendeeRegEnd=now + ten_days,
            onsiteRegStart=now - ten_days,
            onsiteRegEnd=now + ten_days,
            eventStart=now - ten_days,
            eventEnd=now + ten_days,
            collectAddress=True,
            collectBillingAddress=True,
        )

        self.price_levels: dict[str, PriceLevel] = {}
        for tier in self.scenario.tiers:
            pl = PriceLevel.objects.create(
                name=tier.name,
                description=f"Benchmark tier: {tier.name}",
                basePrice=tier.price,
                startDate=now - ten_days,
                endDate=now + ten_days,
                public=True,
                available_to_attendee=True,
                maxCapacity=tier.max_capacity,
            )
            self.price_levels[tier.name] = pl

    def test_run(self):
        report = asyncio.run(self._run_benchmark())
        capacity_ok = report.print_summary()

        # Assert no overselling
        for tier in self.scenario.tiers:
            if tier.max_capacity is not None:
                successes = sum(
                    1 for r in report.tier_results(tier.name) if r.outcome == "success"
                )
                self.assertLessEqual(
                    successes,
                    tier.max_capacity,
                    f"OVERSOLD: {tier.name} had {successes} registrations "
                    f"but only {tier.max_capacity} slots",
                )

    async def _run_benchmark(self):
        server_url = self.live_server_url
        scenario = self.scenario

        # Build user list with staggered start delays
        users: list[tuple[UserSimulator, float]] = []
        user_id = 0
        for tier in scenario.tiers:
            pl = self.price_levels[tier.name]
            for _ in range(tier.num_users):
                sim = UserSimulator(
                    user_id=user_id,
                    tier_name=tier.name,
                    price_level_id=pl.id,
                    event_name=self.event.name,
                    server_url=server_url,
                    think_times=scenario.think_times,
                    retry_delay=scenario.retry_delay,
                    max_retries=scenario.max_retries,
                )
                users.append(sim)
                user_id += 1

        # Shuffle and assign staggered start delays
        random.shuffle(users)
        total_users = len(users)
        delays = [
            (i / max(total_users - 1, 1)) * scenario.ramp_up_seconds
            for i in range(total_users)
        ]

        t_start = time.monotonic()
        tasks = [sim.run(delay) for sim, delay in zip(users, delays)]
        results = await asyncio.gather(*tasks)
        wall_clock = time.monotonic() - t_start

        return BenchmarkReport(scenario, list(results), wall_clock)
