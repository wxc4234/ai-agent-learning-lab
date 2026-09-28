"""Task验证输入准备：重新授权、只读借用、数据编码与独立快照，不启动容器。"""

import asyncio
import base64
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from app.services.runtime.command.task_command_source import borrow_task_command_source
from app.services.runtime.sandbox.sandbox_sample import (
    MAX_SANDBOX_SNAPSHOT_BYTES, SandboxSample, SandboxSampleCreationUnconfirmed,
    create_sandbox_snapshot,
)
from app.services.runtime.sandbox.sandbox_sample_command import _prepare_in_thread
from app.services.runtime.verification.contracts import VerificationPlanId, VerificationRequest
from app.services.workspace.samples.task_sample_binding import TaskSampleBindings
from app.tools.context import ToolExecutionContext

# 留出base64膨胀及固定测试源码的空间；不改变通用文件读取或快照预算。
MAX_TASK_VERIFICATION_BYTES = 64 * 1024

# 样例练习的固定目标是把old\n改为new\n；期望值由服务端决定。
# 仅base64字母表进入字符串字面量，待验证数据永远不拼成可执行语句。
_TEST_PREFIX = b'import base64\nimport unittest\n\nDATA = base64.b64decode("'
_TEST_SUFFIX = b'''", validate=True)

class TaskSampleTest(unittest.TestCase):
    def test_expected_content(self):
        # Keep source contents out of assertion messages.
        self.assertTrue(DATA == b"new\\n", "sample content differs")
'''


def build_task_verification_source(content: bytes) -> bytes:
    """把原始字节放入固定测试；不解码文本、不执行文件内容、不接受期望值覆盖。"""
    if type(content) is not bytes or len(content) > MAX_TASK_VERIFICATION_BYTES:
        raise ValueError('task_verification_input_unavailable')
    source = _TEST_PREFIX + base64.b64encode(content) + _TEST_SUFFIX
    if len(source) > MAX_SANDBOX_SNAPSHOT_BYTES:
        raise ValueError('task_verification_input_unavailable')
    return source


def create_task_verification_snapshot(
    *, context: ToolExecutionContext, bindings: TaskSampleBindings,
) -> SandboxSample:
    """同步内部工厂：预期上下文只用于比对，仍须从数据库重新授权。"""
    with borrow_task_command_source(
        user_id=context.user_id, conversation_id=context.conversation_id,
        bindings=bindings, expected_context=context,
    ) as source:
        # 此时授权事务已经关闭；借用仅覆盖读取，不跨线程泄漏来源句柄。
        content = source.read_sample_bytes()
    # 正常归还后才构造新资源。编码/目标创建失败不能误封锁已归还的Task来源。
    # 用户内容只作为固定测试的数据；禁止直接create_sandbox_snapshot(content=content)。
    trusted_source = build_task_verification_source(content)
    return create_sandbox_snapshot(content=trusted_source)


@dataclass(frozen=True, slots=True)
class PreparedTaskVerificationInput:
    """内部所有者持有的准备结果，不是验证通过或长期Task执行授权。"""

    request: VerificationRequest
    context: ToolExecutionContext
    sample: SandboxSample = field(repr=False)


@dataclass(frozen=True, slots=True)
class TaskVerificationPreparationRecovery:
    """取消期间收回的资源；路径仅供原所有者定位，不能恢复登记或删除权。"""

    plan_id: VerificationPlanId
    context: ToolExecutionContext
    sample: SandboxSample | None = field(default=None, repr=False)
    sample_token: str | None = None
    sample_root: Path | None = field(default=None, repr=False)
    preparation_failed: bool = False


class TaskVerificationPreparationCancelled(asyncio.CancelledError):
    def __init__(self, *, recovery: TaskVerificationPreparationRecovery):
        self.recovery = recovery
        super().__init__('task_verification_preparation_cancelled')


async def prepare_task_verification_input(
    *, request: VerificationRequest, context: ToolExecutionContext,
    bindings: TaskSampleBindings,
) -> PreparedTaskVerificationInput:
    """不持数据库事务等待线程；重复取消也先收回线程结果，再传播取消。

    成功后调用方拥有独立快照；本课不启动Docker。取消不自动删除来源，
    不自动重试或执行。部分创建失败沿用原工厂异常携带的定位证据。
    """
    frozen = VerificationRequest.model_validate(request.model_dump())
    sample, error, cancelled = await _prepare_in_thread(partial(
        create_task_verification_snapshot, context=context, bindings=bindings,
    ))
    if cancelled:
        partial_error = error if isinstance(error, SandboxSampleCreationUnconfirmed) else None
        recovery = TaskVerificationPreparationRecovery(
            plan_id=frozen.plan_id, context=context, sample=sample,
            sample_token=sample.token if sample is not None else partial_error.token if partial_error else None,
            sample_root=sample.root if sample is not None else partial_error.root if partial_error else None,
            preparation_failed=error is not None,
        )
        raise TaskVerificationPreparationCancelled(recovery=recovery) from None
    if error is not None:
        raise error
    if sample is None:
        raise RuntimeError('task_verification_preparation_unconfirmed')
    return PreparedTaskVerificationInput(request=frozen, context=context, sample=sample)
