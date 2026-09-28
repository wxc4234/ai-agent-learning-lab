"""命令与attach采集所需的最小异步字节读取契约。"""

from typing import Protocol


class AsyncByteReader(Protocol):
    # 采集器只依赖read；连接和进程生命周期仍归原执行器所有。
    async def read(self, n: int, /) -> bytes: ...
