"""将单层目录枚举服务适配为工具，身份与扫描上限由服务端控制。"""

import json
from dataclasses import asdict

from pydantic import BaseModel, ConfigDict, Field

from app.repositories.workspace.workspace_repository import (
    WorkspaceNotAccessibleError,
)
from app.services.workspace.directory.workspace_directory import WorkspaceDirectoryError
from app.services.workspace.files.workspace_listing import (
    WorkspaceListingError,
    list_task_directory,
)
from app.services.workspace.directory.workspace_path import WorkspacePathError
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError


class ListDirectoryArguments(BaseModel):
    """模型只能选择项目内相对目录，不能指定身份、递归或数量上限。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    relative_path: str = Field(
        default=".",
        min_length=1,
        max_length=4096,
        description=(
            "当前项目内的相对目录路径，默认 . 表示项目根目录；"
            "使用正斜杠分隔，例如 apps/api；不能使用绝对路径或上级引用"
        ),
    )


def list_directory(
    *,
    context: ToolExecutionContext,
    relative_path: str = ".",
) -> str:
    """返回有限目录结果；已知失败转换为白名单安全错误。"""

    try:
        # context 只提供任务定位，服务仍重新授权并检查路径边界。
        # 不将模型参数与身份字段合并，也不接受模型指定扫描上限。
        result = list_task_directory(
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

    except WorkspaceListingError as error:
        # 只传递明确允许公开的分类，不读取底层异常正文。
        # 将来新增但尚未评审的分类，先退回通用安全错误。
        allowed_codes = {
            "directory_listing_unsupported",
            "directory_listing_not_found",
            "directory_listing_not_directory",
            "directory_listing_access_denied",
            "directory_listing_changed",
            "directory_listing_unavailable",
        }
        code = (
            error.code
            if error.code in allowed_codes
            else "directory_listing_unavailable"
        )
        raise SafeToolExecutionError(code) from None

    # 保留 truncated，不把有限子集描述为完整目录。
    # asdict 将嵌套条目转成普通结构，JSON 将 entries 元组编码成数组。
    return json.dumps(
        asdict(result),
        ensure_ascii=False,
    )
