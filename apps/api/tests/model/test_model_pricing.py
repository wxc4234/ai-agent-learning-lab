"""人民币模型费用计算与价格时段测试。"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.services.runtime.agent_runtime import ModelUsage
from app.services.model.model_pricing import (
    BEIJING_TIMEZONE,
    DeepSeekPricingSchedule,
    ModelPricing,
    estimate_model_cost_cny,
)


PEAK_PRICING = ModelPricing(
    cache_hit_input_cny_per_million=Decimal("0.10"),
    cache_miss_input_cny_per_million=Decimal("3.0"),
    output_cny_per_million=Decimal("9.0"),
)
OFF_PEAK_PRICING = ModelPricing(
    cache_hit_input_cny_per_million=Decimal("0.05"),
    cache_miss_input_cny_per_million=Decimal("1.5"),
    output_cny_per_million=Decimal("4.5"),
)
PRICING_SCHEDULE = DeepSeekPricingSchedule(
    peak=PEAK_PRICING,
    off_peak=OFF_PEAK_PRICING,
)


@pytest.mark.parametrize(
    ("hour", "minute", "expected_tier"),
    [
        (8, 59, "off_peak"),
        (9, 0, "peak"),
        (11, 59, "peak"),
        (12, 0, "off_peak"),
        (13, 59, "off_peak"),
        (14, 0, "peak"),
        (17, 59, "peak"),
        (18, 0, "off_peak"),
    ],
)
def test_pricing_schedule_respects_weekday_boundaries(
    hour: int,
    minute: int,
    expected_tier: str,
):
    priced_at = datetime(
        2026,
        9,
        9,
        hour,
        minute,
        tzinfo=BEIJING_TIMEZONE,
    )

    tier, pricing = PRICING_SCHEDULE.select(priced_at)

    assert tier == expected_tier
    assert pricing is (PEAK_PRICING if expected_tier == "peak" else OFF_PEAK_PRICING)


def test_pricing_schedule_uses_off_peak_rate_on_weekend():
    saturday_morning = datetime(
        2026,
        9,
        12,
        10,
        0,
        tzinfo=BEIJING_TIMEZONE,
    )

    assert PRICING_SCHEDULE.select(saturday_morning) == (
        "off_peak",
        OFF_PEAK_PRICING,
    )


def test_pricing_schedule_converts_utc_to_beijing_time():
    one_am_utc = datetime(
        2026,
        9,
        9,
        1,
        0,
        tzinfo=timezone.utc,
    )

    assert PRICING_SCHEDULE.select(one_am_utc) == (
        "peak",
        PEAK_PRICING,
    )


def test_pricing_schedule_rejects_naive_datetime():
    naive_datetime = datetime(2026, 9, 9, 10, 0)  # noqa: DTZ001

    with pytest.raises(ValueError, match="priced_at 必须包含时区"):
        PRICING_SCHEDULE.select(naive_datetime)


def test_estimate_model_cost_cny_uses_separate_token_prices():
    usage = ModelUsage(
        input_tokens=120,
        output_tokens=35,
        total_tokens=155,
        cache_hit_input_tokens=80,
        cache_miss_input_tokens=40,
    )

    assert estimate_model_cost_cny(usage, PEAK_PRICING) == Decimal("0.00044300")


def test_estimate_model_cost_cny_returns_none_without_usage():
    assert estimate_model_cost_cny(None, PEAK_PRICING) is None


def test_estimate_model_cost_cny_returns_none_without_cache_breakdown():
    usage = ModelUsage(
        input_tokens=120,
        output_tokens=35,
        total_tokens=155,
    )

    assert estimate_model_cost_cny(usage, PEAK_PRICING) is None


def test_estimate_model_cost_cny_returns_none_for_inconsistent_tokens():
    usage = ModelUsage(
        input_tokens=120,
        output_tokens=35,
        total_tokens=154,
        cache_hit_input_tokens=80,
        cache_miss_input_tokens=40,
    )

    assert estimate_model_cost_cny(usage, PEAK_PRICING) is None


def test_estimate_model_cost_cny_returns_none_for_negative_tokens():
    usage = ModelUsage(
        input_tokens=120,
        output_tokens=-1,
        total_tokens=119,
        cache_hit_input_tokens=80,
        cache_miss_input_tokens=40,
    )

    assert estimate_model_cost_cny(usage, PEAK_PRICING) is None


def test_model_pricing_rejects_negative_rate():
    with pytest.raises(
        ValueError,
        match="cache_hit_input_cny_per_million 不能为负数",
    ):
        ModelPricing(
            cache_hit_input_cny_per_million=Decimal("-0.01"),
            cache_miss_input_cny_per_million=Decimal("3.0"),
            output_cny_per_million=Decimal("9.0"),
        )
