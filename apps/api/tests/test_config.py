"""应用配置契约测试。"""

from decimal import Decimal

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
