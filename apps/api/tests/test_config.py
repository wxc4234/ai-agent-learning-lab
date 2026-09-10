"""应用配置契约测试。"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_loads_cny_pricing_from_environment(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv(
        "DEEPSEEK_PEAK_CACHE_HIT_INPUT_CNY_PER_MILLION",
        "0.12",
    )
    monkeypatch.setenv(
        "DEEPSEEK_OFF_PEAK_OUTPUT_CNY_PER_MILLION",
        "4.8",
    )

    loaded_settings = Settings(_env_file=None)

    assert loaded_settings.deepseek_peak_cache_hit_input_cny_per_million == Decimal(
        "0.12"
    )
    assert loaded_settings.deepseek_off_peak_output_cny_per_million == Decimal("4.8")


def test_settings_loads_agent_token_budget_from_environment(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_MAX_TOTAL_TOKENS", "4096")

    loaded_settings = Settings(_env_file=None)

    assert loaded_settings.agent_max_total_tokens == 4096


@pytest.mark.parametrize("invalid_budget", ["0", "-1"])
def test_settings_rejects_non_positive_agent_token_budget(
    monkeypatch,
    invalid_budget: str,
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_MAX_TOTAL_TOKENS", invalid_budget)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
