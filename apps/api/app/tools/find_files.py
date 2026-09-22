"""将有界文件名查找适配为工具，身份与扫描预算由服务端控制。"""

import json
from dataclasses import asdict

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.repositories.workspace.workspace_repository import (
    WorkspaceNotAccessibleError,
)
from app.services.workspace.directory.workspace_directory import WorkspaceDirectoryError
from app.services.workspace.files.workspace_find import (
    MAX_FIND_PATH_CHARACTERS,
    MAX_FIND_QUERY_CHARACTERS,
    WorkspaceFindError,
    find_task_files,
)
from app.services.workspace.directory.workspace_path import WorkspacePathError
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError


class FindFilesArguments(BaseModel):
    """模型只能提供文件名查询和项目内起始目录。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(
        min_length=1,
        max_length=MAX_FIND_QUERY_CHARACTERS,
        description=(
            "区分大小写的文件名字面量查询，保留空格；"
            "不解释通配符或正则，不能包含路径分隔符、换行或NUL"
        ),
    )
    relative_path: str = Field(
        default=".",
        min_length=1,
        max_length=MAX_FIND_PATH_CHARACTERS,
        description=(
            "当前项目内的起始目录，默认 . 表示项目根目录；"
            "使用正斜杠，例如 apps/api；不能使用绝对路径或上级引用"
        ),
    )

    @field_validator("query")
    @classmethod
    def validate_filename_query(cls, value: str) -> str:
        # 不 strip：空格可能就是文件名的一部分。
        # 工具层提前拒绝非法输入，服务层继续保护其他调用入口。
        if any(
            character in value
            for character in ("\x00", "\r", "\n", "/", "\\")
        ):
            raise ValueError(
                "文件名查询不能包含路径分隔符、换行或NUL"
            )

        return value


def find_files(
    *,
    context: ToolExecutionContext,
    query: str,
    relative_path: str = ".",
) -> str:
    """调用已授权查找服务，保留扫描计数与不完整标记。"""

    try:
        # 身份和任务定位只能来自服务端。
        # Context不是永久访问许可，服务仍会查询当前资源归属。
        result = find_task_files(
            user_id=context.user_id,
            workspace_id=context.workspace_id,
            task_id=context.task_id,
            query=query,
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

    except WorkspaceFindError as error:
        # 只公开明确批准的分类，不读取底层异常正文。
        # 将来新增的服务错误在适配前先使用通用安全分类。
        allowed_codes = {
            "invalid_find_query",
            "file_find_unsupported",
            "file_find_changed",
            "file_find_access_denied",
            "file_find_unavailable",
        }
        code = (
            error.code
            if error.code in allowed_codes
            else "file_find_unavailable"
        )
        raise SafeToolExecutionError(code) from None

    # 空结果和截断结果都可以是正常结果。
    # scanned_entries不是匹配总数，不能据此生成“总共找到多少文件”。
    return json.dumps(
        asdict(result),
        ensure_ascii=False,
    )
