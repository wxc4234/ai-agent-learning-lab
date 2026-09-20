"""工具执行所需的服务端上下文，不属于模型可填写的参数。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    """保存本次执行的身份与任务定位，不保存 ORM 对象或目录授权。"""

    # 来源必须是服务端已认证身份，不能从工具 arguments 中提取。
    user_id: int

    # 记录上下文属于哪个会话，后续接入时用于保持执行链路一致。
    conversation_id: str

    # 项目与任务标识由当前会话的数据库关系确定，不由模型选择。
    workspace_id: str
    task_id: str
