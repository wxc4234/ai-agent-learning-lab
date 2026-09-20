"""将受限单文件搜索适配为工具，模型不能控制身份或输出上限。"""

import json
from dataclasses import asdict

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.repositories.workspace.workspace_repository import (
    WorkspaceNotAccessibleError,
)
from app.services.workspace.workspace_directory import WorkspaceDirectoryError
from app.services.workspace.workspace_file import WorkspaceFileError
from app.services.workspace.workspace_path import WorkspacePathError
from app.services.workspace.workspace_search import (
    MAX_SEARCH_QUERY_CHARACTERS,
    WorkspaceSearchError,
    search_task_text_file,
)
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError


class SearchTextFileArguments(BaseModel):
    """只允许文件相对路径和查询文本，不接受额外能力参数。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    relative_path: str = Field(
        min_length=1,
        max_length=4096,
        description=(
            "当前项目内的相对文件路径，使用正斜杠分隔；"
            "不能使用绝对路径或上级引用"
        ),
    )
    query: str = Field(
        min_length=1,
        max_length=MAX_SEARCH_QUERY_CHARACTERS,
        description=(
            "区分大小写的字面量查询文本，保留空格；"
            "不能包含换行或 NUL，不解释正则表达式"
        ),
    )

    @field_validator("query")
    @classmethod
    def reject_line_breaks_and_nul(cls, value: str) -> str:
        # 不 strip，空格本身可以是查询内容。
        # 工具入口提前拒绝非法参数；服务层仍保留自己的校验，
        # 保护其他直接调用服务的入口。
        if any(character in value for character in ("\r", "\n", "\x00")):
            raise ValueError("查询不能包含换行或 NUL 字符")

        return value


def search_text_file(
    *,
    context: ToolExecutionContext,
    relative_path: str,
    query: str,
) -> str:
    """调用已有搜索服务，保留定位与截断语义。"""

    try:
        # 身份与任务定位只来自服务端上下文。
        # 底层读取服务仍重新授权，不把 Context 当作永久访问许可。
        result = search_task_text_file(
            user_id=context.user_id,
            workspace_id=context.workspace_id,
            task_id=context.task_id,
            relative_path=relative_path,
            query=query,
        )

    except WorkspaceSearchError:
        # 保护绕过参数模型、直接调用适配器的路径。
        raise SafeToolExecutionError(
            "invalid_search_query",
        ) from None

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
        # 搜索复用读取边界，因此继续保留可修正的读取失败分类。
        # 不读取底层异常正文，未知文件分类使用固定通用文案。
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

    # 空匹配是成功；截断也是成功，但必须保留标记。
    # 不添加未经完整扫描得到的总匹配数。
    return json.dumps(
        asdict(result),
        ensure_ascii=False,
    )
