"""根据模型 Token 用量和人民币单价估算运行费用。"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal, TypeAlias

from app.services.agent_runtime import ModelUsage


TOKENS_PER_MILLION = Decimal(1_000_000)

# 保留到小数点后 8 位人民币，避免小请求直接显示为 0。
CNY_QUANTUM = Decimal("0.00000001")


# 价格时段与北京时间定义
PricingTier: TypeAlias = Literal["peak", "off_peak"]

BEIJING_TIMEZONE = timezone(
    timedelta(hours=8),
    name="Asia/Shanghai",
)


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """模型每百万 Token 的人民币价格。"""

    cache_hit_input_cny_per_million: Decimal
    cache_miss_input_cny_per_million: Decimal
    output_cny_per_million: Decimal

    def __post_init__(self) -> None:
        rates = {
            "cache_hit_input_cny_per_million": self.cache_hit_input_cny_per_million,
            "cache_miss_input_cny_per_million": self.cache_miss_input_cny_per_million,
            "output_cny_per_million": self.output_cny_per_million,
        }

        for field_name, rate in rates.items():
            if rate < 0:
                raise ValueError(f"{field_name} 不能为负数")


# 一套模型的高峰与空闲价格
@dataclass(frozen=True, slots=True)
class DeepSeekPricingSchedule:
    """DeepSeek 高峰和空闲时段的人民币价格。"""

    peak: ModelPricing
    off_peak: ModelPricing

    def select(
        self,
        priced_at: datetime,
    ) -> tuple[PricingTier, ModelPricing]:
        """根据运行时间选择对应价格。"""

        if priced_at.tzinfo is None or priced_at.utcoffset() is None:
            raise ValueError("priced_at 必须包含时区")

        beijing_time = priced_at.astimezone(BEIJING_TIMEZONE)

        is_weekday = beijing_time.weekday() < 5
        hour = beijing_time.hour

        is_peak_hour = 9 <= hour < 12 or 14 <= hour < 18

        if is_weekday and is_peak_hour:
            return "peak", self.peak

        return "off_peak", self.off_peak


def _has_complete_usage(usage: ModelUsage) -> bool:
    """判断 Token 数据是否完整且内部一致。"""

    cache_hit_tokens = usage.cache_hit_input_tokens
    cache_miss_tokens = usage.cache_miss_input_tokens

    if cache_hit_tokens is None or cache_miss_tokens is None:
        return False

    token_counts = (
        usage.input_tokens,
        usage.output_tokens,
        usage.total_tokens,
        cache_hit_tokens,
        cache_miss_tokens,
    )

    if any(token_count < 0 for token_count in token_counts):
        return False

    if cache_hit_tokens + cache_miss_tokens != usage.input_tokens:
        return False

    return usage.input_tokens + usage.output_tokens == usage.total_tokens


def estimate_model_cost_cny(
    usage: ModelUsage | None,
    pricing: ModelPricing,
) -> Decimal | None:
    """根据完整 Token 用量估算人民币费用。

    usage 缺失、缓存拆分缺失或 Token 数据不一致时返回 None，
    避免用 0 或猜测值伪装成真实费用。
    """

    if usage is None or not _has_complete_usage(usage):
        return None

    cache_hit_tokens = usage.cache_hit_input_tokens
    cache_miss_tokens = usage.cache_miss_input_tokens

    # _has_complete_usage 已经确认这两个字段不是 None。
    assert cache_hit_tokens is not None
    assert cache_miss_tokens is not None

    weighted_cost = (
        Decimal(cache_hit_tokens) * pricing.cache_hit_input_cny_per_million
        + Decimal(cache_miss_tokens) * pricing.cache_miss_input_cny_per_million
        + Decimal(usage.output_tokens) * pricing.output_cny_per_million
    )

    cost_cny = weighted_cost / TOKENS_PER_MILLION

    return cost_cny.quantize(
        CNY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
