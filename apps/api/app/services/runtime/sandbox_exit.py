"""确认已退出容器的结果，不读取输出或推断超时/取消原因。"""

import json
from dataclasses import dataclass

from app.services.runtime.command_contracts import CommandRequest
from app.services.runtime.sandbox_identity import SandboxContainerIdentity, SandboxIdentityError
from app.services.runtime.sandbox_spec import build_sandbox_create_spec
from app.services.runtime.sandbox_stop import confirm_sandbox_state


class SandboxExitUnconfirmed(ValueError):
    """缺少一致的退出证据，不能报告命令成功。"""

    def __init__(self) -> None:
        super().__init__("Sandbox 退出结果未确认")


@dataclass(frozen=True, slots=True)
class SandboxExitResult:
    """退出事实快照；不包含尚未采集的stdout/stderr或执行耗时。"""

    identity: SandboxContainerIdentity
    exit_code: int
    oom_killed: bool
    daemon_error: bool

    @property
    def succeeded(self) -> bool:
        # 退出码0是必要条件；OOM和daemon错误也不能被忽略。
        # 这仅表示进程结果成功，不代表命令完成了用户的业务目标。
        return self.exit_code == 0 and not self.oom_killed and not self.daemon_error


def confirm_sandbox_exit(
    *, request: CommandRequest, execution_token: str,
    container_id: str, inspect_stdout: str,
) -> SandboxExitResult:
    """对同一份有界严格响应核对身份、退出状态和结果字段。"""

    # 原执行请求错误仍是调用前校验错误，不伪装为daemon结果。
    spec = build_sandbox_create_spec(request=request, execution_token=execution_token)
    try:
        snapshot = confirm_sandbox_state(
            container_id=container_id, inspect_stdout=inspect_stdout, spec=spec,
        )
    except SandboxIdentityError:
        raise SandboxExitUnconfirmed() from None

    # created虽已停止，但命令尚未执行；只有exited可以解释退出码。
    if snapshot.status != "exited" or not snapshot.stopped:
        raise SandboxExitUnconfirmed()

    # 状态解析已校验长度、重复字段、非标准JSON和身份。
    # 重读同一不可变字符串，不拼接不同查询时刻的数据。
    state = json.loads(inspect_stdout)[0]["State"]
    exit_code = state.get("ExitCode")
    oom_killed = state.get("OOMKilled")
    error = state.get("Error")

    # 当前策略只接受Linux容器退出码，不与宿主客户端负信号码混用。
    if type(exit_code) is not int or not 0 <= exit_code <= 255:
        raise SandboxExitUnconfirmed()
    if type(oom_killed) is not bool or not isinstance(error, str):
        raise SandboxExitUnconfirmed()

    # 原始daemon错误可能包含路径或命令参数，只保留是否存在错误。
    # 不从137推断OOM，也不从143推断用户取消；原因需编排层另行记录。
    return SandboxExitResult(
        identity=snapshot.identity, exit_code=exit_code,
        oom_killed=oom_killed, daemon_error=error != "",
    )
