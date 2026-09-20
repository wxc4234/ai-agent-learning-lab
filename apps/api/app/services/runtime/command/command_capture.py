"""并发排空命令的两路输出，并等待读取任务完成收尾。"""

import asyncio
from dataclasses import dataclass

from app.services.runtime.command.command_output import CapturedCommandOutput
from app.services.runtime.command.command_stream import drain_command_output


@dataclass(frozen=True, slots=True)
class CapturedCommandStreams:
    """两路均正常读到 EOF 后得到的输出快照。"""

    stdout: CapturedCommandOutput
    stderr: CapturedCommandOutput


async def drain_command_streams(
    *,
    stdout: asyncio.StreamReader,
    stderr: asyncio.StreamReader,
) -> CapturedCommandStreams:
    """并发读取 stdout/stderr；失败或取消时等待读取任务退出。"""

    # 一个流只能由一个读取者消费。
    # 同一对象传给两路会争抢数据，不能把它当成两个独立输出源。
    if stdout is stderr:
        raise ValueError("stdout 和 stderr 必须是不同的输出流")

    # TaskGroup 拥有内部两个读取任务，不向外暴露任务句柄。
    # 普通异常触发另一任务取消；离开上下文前会等待任务收尾。
    async with asyncio.TaskGroup() as group:
        stdout_task = group.create_task(
            drain_command_output(stdout),
            name="command-stdout-drain",
        )
        stderr_task = group.create_task(
            drain_command_output(stderr),
            name="command-stderr-drain",
        )

    # 正常离开 TaskGroup 后再取结果，不在 finally 中返回。
    # 若任务失败或整体取消，不会把另一侧的部分成功冒充完整结果。
    return CapturedCommandStreams(
        stdout=stdout_task.result(),
        stderr=stderr_task.result(),
    )
