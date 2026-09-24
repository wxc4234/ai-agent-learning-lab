"""应用持有Git样例；失败现场只留给可信宿主，不公开HTTP恢复入口。"""

import asyncio
from dataclasses import dataclass

from fastapi import FastAPI, Request

from app.services.workspace.git.task_git_samples import TaskGitSampleError, TaskGitSamples


@dataclass
class _Owner:
    manager: TaskGitSamples
    accepting: bool = True


def start_git_samples(application: FastAPI) -> None:
    # 不覆盖未完成关闭的所有者，防止遗失上下文管理器而触发隐式清理。
    if hasattr(application.state, 'git_samples'):
        raise TaskGitSampleError()
    application.state.git_samples = _Owner(TaskGitSamples())


def get_git_samples(request: Request) -> TaskGitSamples:
    """内部依赖只从当前应用取实例，模型参数没有manager或路径入口。"""
    owner = getattr(request.app.state, 'git_samples', None)
    if not isinstance(owner, _Owner) or not owner.accepting:
        raise TaskGitSampleError()
    return owner.manager


async def stop_git_samples(application: FastAPI) -> None:
    owner = getattr(application.state, 'git_samples', None)
    if owner is None:
        return
    # 第二次并发关闭或失败后重入不自动重试；保留现场供宿主诊断。
    if not owner.accepting:
        raise TaskGitSampleError()
    owner.accepting = False
    owner.manager.stop_accepting()
    # 文件清理放在线程；取消等待不代表线程已停止，必须等待真实结果。
    worker = asyncio.create_task(asyncio.to_thread(owner.manager.shutdown))
    cancelled = False
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancelled = True
    # 异常时保留封闭的owner；仅确认成功才释放应用引用。
    worker.result()
    del application.state.git_samples
    if cancelled:
        raise asyncio.CancelledError()
