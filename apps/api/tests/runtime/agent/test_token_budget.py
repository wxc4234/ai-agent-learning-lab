"""单次 Agent 运行 Token 预算策略测试。"""

import pytest

from app.services.runtime.agent.token_budget import evaluate_token_budget


@pytest.mark.parametrize("total_tokens", [0, 99])
def test_token_budget_is_within_budget_below_limit(total_tokens: int):
    assert (
        evaluate_token_budget(total_tokens, max_total_tokens=100)
        == "within_budget"
    )


@pytest.mark.parametrize("total_tokens", [100, 101])
def test_token_budget_is_exhausted_at_or_above_limit(total_tokens: int):
    assert (
        evaluate_token_budget(total_tokens, max_total_tokens=100)
        == "exhausted"
    )


def test_token_budget_keeps_missing_usage_unknown():
    assert evaluate_token_budget(None, max_total_tokens=100) == "unknown"


@pytest.mark.parametrize("max_total_tokens", [0, -1, 1.5, True, None])
def test_token_budget_rejects_invalid_limits(max_total_tokens: object):
    with pytest.raises(
        ValueError,
        match="max_total_tokens 必须是大于等于 1 的整数",
    ):
        evaluate_token_budget(10, max_total_tokens=max_total_tokens)  # type: ignore[arg-type]


@pytest.mark.parametrize("total_tokens", [-1, 1.5, True, "10"])
def test_token_budget_rejects_invalid_usage(total_tokens: object):
    with pytest.raises(
        ValueError,
        match="total_tokens 必须是大于等于 0 的整数或 None",
    ):
        evaluate_token_budget(total_tokens, max_total_tokens=100)  # type: ignore[arg-type]
