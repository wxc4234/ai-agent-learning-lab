"""将已确认的 Sandbox 执行结果转换为统一命令结果，不执行外部操作。"""

from app.services.runtime.command.command_contracts import CommandResult
from app.services.runtime.sandbox.sandbox_execution import SandboxExecutionResult


def build_command_result(
    execution: SandboxExecutionResult,
) -> CommandResult:
    """仅适配内部编排器正常返回的结果，不转换异常或重新授权容器。"""

    if not isinstance(execution, SandboxExecutionResult):
        raise TypeError("execution 必须是 SandboxExecutionResult")

    # 通用命令契约保留了POSIX负返回码的表达空间；
    # 这里转换的是Docker退出事实，继续遵守严格整数0～255的约束。
    exit_code = execution.exit.exit_code

    if type(exit_code) is not int or not 0 <= exit_code <= 255:
        raise ValueError("Docker 退出码必须是0～255的整数")

    # 显式选择公开字段，不直接展开内部dataclass。
    # execution_token、container_id及原始daemon错误正文不进入命令结果。
    return CommandResult(
        status="exited",
        exit_code=exit_code,
        oom_killed=execution.exit.oom_killed,
        daemon_error=execution.exit.daemon_error,
        stdout=execution.streams.stdout.text,
        stderr=execution.streams.stderr.text,
        stdout_truncated=execution.streams.stdout.truncated,
        stderr_truncated=execution.streams.stderr.truncated,
        # 沿用编排器测得的耗时，不重新计时或伪造进程精确运行时长。
        duration_ms=execution.duration_ms,
    )
