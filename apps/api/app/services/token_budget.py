"""单次 Agent 运行的 Token 预算策略。"""

from typing import Literal, TypeAlias

# 预算判断只返回稳定状态，不在这里终止 Agent Loop
TokenBudgetStatus: TypeAlias = Literal[
    "within_budget",
    "exhausted",
    "unknown",
]


def _is_integer(value: object) -> bool:
    """只接受真正的整数，避免把 bool 当作 0 或 1。"""

    return isinstance(value, int) and not isinstance(value, bool)


def evaluate_token_budget(
    total_tokens: int | None,
    *,
    max_total_tokens: int,
) -> TokenBudgetStatus:
    """判断累计 Token 是否允许 Agent 开始下一次模型调用。"""

    # 配置错误必须尽早暴露，不能静默使用错误预算。
    if not _is_integer(max_total_tokens) or max_total_tokens < 1:
        raise ValueError("max_total_tokens 必须是大于等于 1 的整数")

    if total_tokens is None:
        return "unknown"

    if not _is_integer(total_tokens) or total_tokens < 0:
        raise ValueError("total_tokens 必须是大于等于 0 的整数或 None")

    # 刚好达到上限时也不能继续下一次模型请求。
    if total_tokens >= max_total_tokens:
        return "exhausted"

    return "within_budget"
