"""可信样例的内部验证适配；不接受宿主路径，不注册模型工具。"""

from dataclasses import dataclass
from functools import partial

from pydantic import ValidationError

from app.services.runtime.sandbox.sandbox_sample import create_sandbox_snapshot
from app.services.runtime.sandbox.sandbox_sample_command import SampleCommandResult, _run_owned_sample_command
from app.services.runtime.verification.contracts import (
    VerificationRequest, VerificationResult, build_verification_command,
)
from app.services.runtime.verification.report_parser import VerificationReportError, parse_verification_report


@dataclass(frozen=True, slots=True)
class SandboxVerificationResult:
    # 完整保留生命周期回执；验证失败与清理失败是不同事实。
    execution: SampleCommandResult
    verification: VerificationResult
    report_error: str | None


class VerificationAdaptationError(RuntimeError):
    """执行已结束但事实冲突；携带原回执，不能丢失清理证据或重跑。"""

    def __init__(self, execution: SampleCommandResult):
        self.execution = execution
        super().__init__('verification_evidence_invalid')


def adapt_sample_verification(execution: SampleCommandResult) -> SandboxVerificationResult:
    """只适配生命周期正常返回，不将取消/超时恢复快照拼成成功结果。"""
    report = None
    error = None
    try:
        # 固定采集器只写ASCII；拒绝通用解码器的替换字符，避免恢复有损原字节。
        data = execution.command.stdout.encode('ascii', errors='strict')
        report = parse_verification_report(data, truncated=execution.command.stdout_truncated)
    except UnicodeEncodeError:
        error = 'verification_report_invalid_encoding'
    except VerificationReportError as failure:
        error = failure.code
    try:
        value = VerificationResult(
            plan_id='sample_unittest_v1', command=execution.command, report=report,
        )
    except ValidationError:
        raise VerificationAdaptationError(execution) from None
    return SandboxVerificationResult(execution=execution, verification=value, report_error=error)


async def run_sample_verification(
    *, request: VerificationRequest, trusted_test_source: bytes,
) -> SandboxVerificationResult:
    """仅可信服务端样例源码；bytes类型不构成信任凭据，禁止直接绑定用户输入。

    独立来源在工作线程创建，生命周期服务重新核对登记、身份和隔离策略。
    超时/取消/清理失败原样传播SampleCommand异常及恢复信息，不自动重试。
    Task授权和借用将在后续单独接入，不在本入口隐式查询或推断。
    """
    frozen = VerificationRequest.model_validate(request.model_dump())
    command = build_verification_command(frozen.plan_id)
    execution = await _run_owned_sample_command(
        request=command,
        prepare_sample=partial(create_sandbox_snapshot, content=trusted_test_source),
        prepare_in_thread=True,
    )
    return adapt_sample_verification(execution)
