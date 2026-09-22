"""把受限文本读取服务适配为工具，不允许模型选择身份或根目录。"""

import json
from dataclasses import asdict

from pydantic import BaseModel, ConfigDict, Field

from app.repositories.workspace.workspace_repository import (
    WorkspaceNotAccessibleError,
)
from app.services.workspace.directory.workspace_directory import WorkspaceDirectoryError
from app.services.workspace.files.workspace_file import (
    WorkspaceFileError,
    read_task_text_file,
)
from app.services.workspace.directory.workspace_path import WorkspacePathError
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError


class ReadTextFileArguments(BaseModel):
    """模型只负责提出相对路径，身份和任务定位由服务端注入。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    relative_path: str = Field(
        min_length=1,
        max_length=4096,
        description=(
            "当前项目内的相对文件路径，使用正斜杠分隔，"
            "例如 apps/api/app/main.py；不能使用绝对路径或上级引用"
        ),
    )


def read_text_file(
    *,
    context: ToolExecutionContext,
    relative_path: str,
) -> str:
    """读取文本并返回结构化 JSON；已知业务失败使用安全异常。"""

    try:
        # 上下文仅提供定位，读取服务仍重新检查任务归属与目录边界。
        # 不接收模型提供的 user_id、workspace_id、task_id 或 root_path。
        result = read_task_text_file(
            user_id=context.user_id,
            workspace_id=context.workspace_id,
            task_id=context.task_id,
            relative_path=relative_path,
        )

    except WorkspaceNotAccessibleError:
        raise SafeToolExecutionError(
            "workspace_not_accessible",
        ) from None

    except WorkspaceDirectoryError:
        raise SafeToolExecutionError(
            "workspace_directory_unavailable",
        ) from None

    except WorkspacePathError as error:
        code = (
            "workspace_directory_unbound"
            if error.code == "workspace_directory_unbound"
            else "workspace_path_rejected"
        )
        raise SafeToolExecutionError(code) from None

    except WorkspaceFileError as error:
        # 只保留有助于模型修正操作的已知分类。
        # 不读取 str(error)，其他文件故障统一使用固定文案。
        actionable_codes = {
            "file_read_unsupported",
            "file_not_regular",
            "file_too_large",
            "file_not_utf8_text",
            "file_changed",
        }
        code = (
            error.code
            if error.code in actionable_codes
            else "file_unavailable"
        )
        raise SafeToolExecutionError(code) from None

    # JSON 保留内容、相对路径和实际字节数，不暴露绝对路径。
    # 未知异常不在这里包装，由 Runtime 的通用失败分支处理。
    return json.dumps(
        asdict(result),
        ensure_ascii=False,
    )
