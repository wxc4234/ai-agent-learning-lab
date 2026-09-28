"""固定验证的工具协议与纯投影；不装配请求能力、不启动Sandbox。"""

import json
from typing import Protocol

from pydantic import ValidationError

from app.services.runtime.verification.contracts import VerificationRequest, VerificationResult
from app.services.runtime.verification.sandbox_verification import (
    SandboxVerificationResult, adapt_sample_verification,
)
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError
from app.tools.registry import ToolDefinition

MAX_PUBLIC_RESULT_BYTES = 4096

# 直接复用唯一参数契约，避免工具Schema与执行服务对计划范围产生分歧。
TaskVerificationArguments = VerificationRequest


class RecordedVerificationExecutor(Protocol):
    """可信宿主必须先保留生命周期回执/恢复信息，再返回或传播异常。

    该接口只是装配约定，不证明实现已记录。本课不提供默认实现，
    禁止把未带恢复登记的run_task_verification直接接入请求注册表。
    """

    async def __call__(
        self, *, request: VerificationRequest, context: ToolExecutionContext,
    ) -> SandboxVerificationResult: ...


def project_task_verification_result(value: SandboxVerificationResult) -> str:
    """正常生命周期回执的白名单投影；不展开内部对象或反射私有错误。"""
    try:
        if type(value) is not SandboxVerificationResult or value.execution.sample_cleaned is not True:
            raise ValueError('incomplete lifecycle')
        # frozen并不能阻止model_copy/model_construct或嵌套对象被改坏。
        verified = VerificationResult.model_validate(value.verification.model_dump())
        if value.execution.command.model_dump() != verified.command.model_dump():
            raise ValueError('inconsistent execution receipt')
        # 重新从回执中的报告流派生计数，拒绝单独伪造更好看的验证结果。
        canonical = adapt_sample_verification(value.execution)
        if canonical.verification.model_dump() != verified.model_dump():
            raise ValueError('inconsistent report receipt')
        report = verified.report
        # 不透传report_error正文；报告状态只能从已校验报告是否存在派生。
        if (report is not None) != (value.report_error is None):
            raise ValueError('inconsistent report state')
        command = verified.command
        counts = None if report is None else {
            'tests_run': report.tests_run,
            'successful_tests': report.successful_tests,
            'failures': report.failures,
            'errors': report.errors,
            'skipped': report.skipped,
            'expected_failures': report.expected_failures,
            'unexpected_successes': report.unexpected_successes,
        }
        payload = {
            'source': 'trusted_sample_snapshot',
            'scope': 'controlled_sample_only',
            'plan_id': verified.plan_id,
            'outcome': verified.outcome,
            'command': {
                'status': command.status,
                'exit_code': command.exit_code,
                'oom_killed': command.oom_killed,
                'daemon_error': command.daemon_error,
                'stdout_truncated': command.stdout_truncated,
                'stderr_truncated': command.stderr_truncated,
                'duration_ms': command.duration_ms,
            },
            'report_status': 'complete' if report is not None else 'unavailable',
            'counts': counts,
        }
        encoded = json.dumps(payload, ensure_ascii=True, separators=(',', ':'), allow_nan=False)
        if len(encoded.encode('utf-8')) > MAX_PUBLIC_RESULT_BYTES:
            raise SafeToolExecutionError('verification_result_too_large')
        return encoded
    except SafeToolExecutionError:
        raise
    except Exception:  # noqa: BLE001 -- 投影失败不公开原文、路径或部分成功结果。
        raise SafeToolExecutionError('verification_result_unavailable') from None


def make_task_verification_definition(recorded_executor: RecordedVerificationExecutor) -> ToolDefinition:
    """只构造定义，不注册。宿主须注入已保存恢复信息的Task执行服务。"""
    if not callable(recorded_executor):
        raise TypeError('需要服务端已登记恢复信息的异步执行器')

    async def execute(*, context: ToolExecutionContext, **arguments: object) -> str:
        if not isinstance(context, ToolExecutionContext):
            raise SafeToolExecutionError('workspace_not_accessible')
        try:
            request = TaskVerificationArguments.model_validate(arguments)
        except ValidationError:
            raise SafeToolExecutionError('verification_request_rejected') from None
        # 不捕获生命周期异常/取消，不将恢复记录转成JSON或自行清理、重试。
        result = await recorded_executor(request=request, context=context)
        return project_task_verification_result(result)

    return ToolDefinition(
        name='verify_task_sample',
        description=(
            '验证当前Task的服务端受控文本样例，仅支持sample_unittest_v1。'
            '固定测试检查example.txt原始字节是否精确等于new加一个LF换行；'
            '不执行用户文件内容，不测试普通项目，不安装依赖，不修改原Task文件。'
            '参数仅plan_id；身份、路径、期望值、命令和预算由服务端决定。'
            'outcome为passed、failed或unconfirmed；passed仅证明本次样例快照符合固定目标。'
            '报告不可用、零测试、全跳过或输出截断不能证明通过。'
            '调用可能创建临时资源；遇到取消、未知执行或清理状态不能自动重试。'
        ),
        arguments_model=TaskVerificationArguments,
        async_executor=execute,
        requires_context=True,
        timeout_seconds=60.0,
    )
