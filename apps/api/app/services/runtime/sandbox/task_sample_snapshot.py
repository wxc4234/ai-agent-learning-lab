"""从已授权 Task 样例导出独立 Sandbox 快照，不启动容器。"""

from app.services.runtime.command.task_command_source import (
    borrow_task_command_source,
)
from app.services.runtime.sandbox.sandbox_sample import (
    SandboxSample,
    create_sandbox_snapshot,
)
from app.services.workspace.samples.task_sample_binding import TaskSampleBindings
from app.tools.context import ToolExecutionContext


def create_task_sandbox_snapshot(
    *,
    user_id: int,
    conversation_id: str,
    bindings: TaskSampleBindings,
    expected_context: ToolExecutionContext | None = None,
) -> SandboxSample:
    """身份来自服务端，任务由当前会话确定，不接受宿主路径。"""

    # 借用覆盖完整读取过程；底层负责归属、绑定、独占与异常封锁。
    with borrow_task_command_source(
        user_id=user_id,
        conversation_id=conversation_id,
        bindings=bindings,
        **({} if expected_context is None else {"expected_context": expected_context}),
    ) as source:
        content = source.read_sample_bytes()

    # 必须等借用正常退出后才创建目标。
    # 此时 content 是独立 bytes，Task 来源随后变化不影响这些字节。
    # 若归还借用失败，执行不会到达这里，不会产生新的目标现场。
    #
    # 返回后由调用方拥有快照。创建失败时沿用工厂异常携带部分现场；
    # 本入口不自动重试、不删除 Task 来源，也不启动 Docker。
    return create_sandbox_snapshot(content=content)
