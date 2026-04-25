from decimal import Decimal

from .benchmark import BaseBenchmark, ScenarioConfig, ThinkTimes, TierConfig


class SmallCapacityRush(BaseBenchmark):
    """Quick smoke test: 50 users fight for 10 VIP slots, 50 get unlimited Standard."""

    scenario = ScenarioConfig(
        name="SmallCapacityRush",
        tiers=[
            TierConfig(
                name="VIP", price=Decimal("150.00"), max_capacity=10, num_users=50
            ),
            TierConfig(
                name="Standard", price=Decimal("55.00"), max_capacity=None, num_users=50
            ),
        ],
        ramp_up_seconds=5.0,
        think_times=ThinkTimes(
            after_landing=(1.0, 2.0),
            after_tier_select=(3.0, 8.0),
            after_add_cart=(1.0, 3.0),
            after_view_cart=(1.0, 5.0),
        ),
        retry_delay=(3.0, 8.0),
        max_retries=10,
    )


# class LargeScaleRegistration(BaseBenchmark):
#     """Realistic medium-scale scenario: 900 users across 3 tiers."""

#     scenario = ScenarioConfig(
#         name="LargeScaleRegistration",
#         tiers=[
#             TierConfig(
#                 name="Early Bird",
#                 price=Decimal("45.00"),
#                 max_capacity=100,
#                 num_users=300,
#             ),
#             TierConfig(
#                 name="Regular", price=Decimal("60.00"), max_capacity=None, num_users=500
#             ),
#             TierConfig(
#                 name="VIP", price=Decimal("150.00"), max_capacity=20, num_users=100
#             ),
#         ],
#         ramp_up_seconds=30.0,
#         retry_delay=(5.0, 15.0),
#         max_retries=10,
#     )


class ExtremeContention(BaseBenchmark):
    """Stress test: 200 users fighting for 5 slots with minimal think time."""

    scenario = ScenarioConfig(
        name="ExtremeContention",
        tiers=[
            TierConfig(
                name="Limited", price=Decimal("50.00"), max_capacity=5, num_users=200
            ),
        ],
        ramp_up_seconds=3.0,
        think_times=ThinkTimes(
            after_landing=(0.5, 1.0),
            after_tier_select=(1.0, 3.0),
            after_add_cart=(0.5, 1.0),
            after_view_cart=(0.5, 2.0),
        ),
        retry_delay=(2.0, 5.0),
        max_retries=15,
    )


# class CapacityRush(BaseBenchmark):
#     """Quick smoke test: 50 users fight for 10 VIP slots, 50 get unlimited Standard."""

#     scenario = ScenarioConfig(
#         name="SmallCapacityRush",
#         tiers=[
#             TierConfig(
#                 name="VIP", price=Decimal("150.00"), max_capacity=50, num_users=100
#             ),
#             TierConfig(
#                 name="Standard",
#                 price=Decimal("55.00"),
#                 max_capacity=None,
#                 num_users=200,
#             ),
#         ],
#         ramp_up_seconds=5.0,
#         think_times=ThinkTimes(
#             after_landing=(1.0, 2.0),
#             after_tier_select=(3.0, 8.0),
#             after_add_cart=(1.0, 3.0),
#             after_view_cart=(1.0, 5.0),
#         ),
#         retry_delay=(3.0, 8.0),
#         max_retries=10,
#     )


# class BigRushedCon(BaseBenchmark):
#     """5000 person con. 50 VIP slots. 5 super VIP slots."""

#     scenario = ScenarioConfig(
#         name="ExtremeContention",
#         tiers=[
#             TierConfig(
#                 name="SuperVIP", price=Decimal("1000.00"), max_capacity=5, num_users=200
#             ),
#             TierConfig(
#                 name="VIP", price=Decimal("700.00"), max_capacity=50, num_users=200
#             ),
#             TierConfig(
#                 name="Normal", price=Decimal("50.00"), max_capacity=None, num_users=5000
#             ),
#         ],
#         ramp_up_seconds=1.0,
#         think_times=ThinkTimes(
#             after_landing=(3.0, 12.0),
#             after_tier_select=(1.0, 3.0),
#             after_add_cart=(0.5, 1.0),
#             after_view_cart=(0.5, 2.0),
#         ),
#         retry_delay=(2.0, 5.0),
#         max_retries=15,
#     )
