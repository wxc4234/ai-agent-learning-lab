"""同一事件循环内共享的执行并发预算，容量满时立即拒绝。"""

import asyncio
from collections.abc import Generator
from contextlib import contextmanager


class ExecutionCapacityExceededError(Exception):
    """当前预算已满；调用方决定如何映射为安全响应。"""

    code = "execution_capacity_exceeded"

    def __init__(self) -> None:
        super().__init__("当前执行数量已达上限，请稍后再试")


class ExecutionBudget:
    """通过作用域获取和归还名额，不提供独立的公开释放方法。"""

    def __init__(self, *, capacity: int) -> None:
        # bool 是 int 的子类，必须单独排除。
        # 容量必须明确为正整数，不能接受小数或字符串后隐式转换。
        if (
            isinstance(capacity, bool)
            or not isinstance(capacity, int)
            or capacity <= 0
        ):
            raise ValueError("capacity 必须为正整数")

        self._capacity = capacity
        self._in_use = 0

        # 首次使用时绑定事件循环，允许在应用装配阶段先创建实例。
        # 后续禁止跨循环使用，避免把单循环计数误当成线程安全组件。
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def capacity(self) -> int:
        """预算总容量；实例创建后不动态调整。"""
        return self._capacity

    @property
    def in_use(self) -> int:
        """当前已取得的名额数，仅用于观察。"""
        return self._in_use

    @contextmanager
    def reserve(self) -> Generator[None, None, None]:
        """取得一个名额；容量满时拒绝，退出时归还。"""

        loop = asyncio.get_running_loop()

        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError("ExecutionBudget 只能在绑定的事件循环中使用")

        # 检查和递增之间没有 await。
        # 同一事件循环中的其他协程无法在这段同步代码中间插入执行。
        if self._in_use >= self._capacity:
            raise ExecutionCapacityExceededError()

        self._in_use += 1

        try:
            yield
        finally:
            # 只有成功取得名额的调用才会进入这里。
            # 归还不包含 await，异常或取消不会在归还中间再次打断。
            self._in_use -= 1
