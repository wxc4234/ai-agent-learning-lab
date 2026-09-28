"""验证计划和结果的纯数据边界；不读取目录、不启动进程或安装依赖。"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.runtime.command.command_contracts import (
    COMMAND_TIMEOUT_SECONDS, MAX_CAPTURE_BYTES_PER_STREAM,
    CommandRequest, CommandResult,
)
from app.services.runtime.sandbox.sandbox_spec import APPROVED_SANDBOX_IMAGE
from app.services.runtime.verification.sandbox_runner_source import SANDBOX_UNITTEST_SOURCE

VerificationPlanId = Literal['sample_unittest_v1']
MAX_TEST_COUNT = 10_000


class VerificationRequest(BaseModel):
    """调用方仅选择登记计划，身份和样例来源必须由后续服务重新授权。"""

    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    plan_id: VerificationPlanId


@dataclass(frozen=True, slots=True)
class VerificationPlan:
    """固定策略快照不是执行许可；不能直接将调用方构造的实例交给执行器。"""

    plan_id: VerificationPlanId
    source: Literal['trusted_sample_snapshot']
    image: str
    argv: tuple[str, ...]
    working_directory: str
    timeout_seconds: int
    max_capture_bytes_per_stream: int


# 首版只约定可信样例的固定测试文件；-I隔离Python环境，-B避免写pycache。
# 不发现整个项目、不读取项目配置、不允许pip或模型提供pytest附加选项。
_PLANS = MappingProxyType({
    'sample_unittest_v1': VerificationPlan(
        plan_id='sample_unittest_v1',
        source='trusted_sample_snapshot',
        image=APPROVED_SANDBOX_IMAGE,
        argv=(
            '/usr/local/bin/python', '-I', '-B', '-c', SANDBOX_UNITTEST_SOURCE,
        ),
        working_directory='.',
        timeout_seconds=COMMAND_TIMEOUT_SECONDS,
        max_capture_bytes_per_stream=MAX_CAPTURE_BYTES_PER_STREAM,
    ),
})


def resolve_verification_plan(plan_id: str) -> VerificationPlan:
    """只从服务端表查找；不接受外部计划实例或动态模块名。"""
    if type(plan_id) is not str or plan_id not in _PLANS:
        raise ValueError('verification_plan_unavailable')
    return _PLANS[plan_id]


def build_verification_command(plan_id: str) -> CommandRequest:
    """每次返回独立命令，调用方修改列表不会污染固定登记。"""
    plan = resolve_verification_plan(plan_id)
    return CommandRequest(argv=list(plan.argv), working_directory=plan.working_directory)


class VerificationTestReport(BaseModel):
    """固定采集器提供的完整计数；不从stdout中的OK或模型文字推断。"""

    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    plan_id: VerificationPlanId
    tests_run: int = Field(ge=0, le=MAX_TEST_COUNT)
    successful_tests: int = Field(ge=0, le=MAX_TEST_COUNT)
    failures: int = Field(ge=0, le=MAX_TEST_COUNT)
    errors: int = Field(ge=0, le=MAX_TEST_COUNT)
    skipped: int = Field(ge=0, le=MAX_TEST_COUNT)
    expected_failures: int = Field(ge=0, le=MAX_TEST_COUNT)
    unexpected_successes: int = Field(ge=0, le=MAX_TEST_COUNT)

    @model_validator(mode='after')
    def validate_counts(self) -> Self:
        # 只有方法级完整成功/预期失败/意外成功与tests_run可相加比较。
        # fixture错误可在0项时发生，subTest的错误/跳过条数可超过方法数。
        if self.successful_tests + self.expected_failures + self.unexpected_successes > self.tests_run:
            raise ValueError('verification_counts_inconsistent')
        return self

    @property
    def has_failures(self) -> bool:
        return bool(self.failures or self.errors or self.unexpected_successes)


class VerificationResult(BaseModel):
    """可信执行适配器的内部结果；状态由证据派生，不接受外部passed字段。"""

    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    plan_id: VerificationPlanId
    source: Literal['trusted_sample_snapshot'] = 'trusted_sample_snapshot'
    command: CommandResult
    report: VerificationTestReport | None = None

    @field_validator('command', mode='before')
    @classmethod
    def revalidate_command(cls, value: object) -> object:
        # frozen/model_construct/model_copy都不是信任边界，重新校验嵌套实例。
        if isinstance(value, CommandResult):
            return CommandResult.model_validate(value.model_dump())
        return value

    @field_validator('report', mode='before')
    @classmethod
    def revalidate_report(cls, value: object) -> object:
        if isinstance(value, VerificationTestReport):
            return VerificationTestReport.model_validate(value.model_dump())
        return value

    @model_validator(mode='after')
    def validate_evidence(self) -> Self:
        resolve_verification_plan(self.plan_id)
        if self.report is not None:
            if self.report.plan_id != self.plan_id or self.command.status != 'exited':
                raise ValueError('verification_report_not_completed')
            if self.command.exit_code == 0 and self.report.has_failures:
                raise ValueError('verification_exit_report_conflict')
        # 显示文本另限UTF-8字节；原始捕获预算由Sandbox执行器实施，文本校验不替代捕获限制。
        for text in (self.command.stdout, self.command.stderr):
            try:
                size = len(text.encode('utf-8'))
            except UnicodeEncodeError:
                raise ValueError('verification_output_invalid_encoding') from None
            if size > MAX_CAPTURE_BYTES_PER_STREAM:
                raise ValueError('verification_output_limit')
        return self

    @property
    def outcome(self) -> Literal['passed', 'failed', 'unconfirmed']:
        # 超时/取消即便获得exit 0也不是本轮验证通过；缺失退出错误事实保留未知。
        if self.command.status != 'exited':
            return 'unconfirmed'
        if self.command.exit_code != 0 or self.command.oom_killed is True or self.command.daemon_error is True:
            return 'failed'
        if (not self.command.succeeded or self.command.stdout_truncated
                or self.command.stderr_truncated or self.report is None):
            return 'unconfirmed'
        # 必须至少有一个完整通过的方法；部分subTest成功不补成方法通过。
        if self.report.successful_tests == 0:
            return 'unconfirmed'
        return 'passed'
