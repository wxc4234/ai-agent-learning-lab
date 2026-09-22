"""将授权文件预览适配为只读工具，不执行文件修改。"""

import json

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.repositories.workspace.workspace_repository import (
    WorkspaceNotAccessibleError,
)
from app.services.workspace.directory.workspace_directory import WorkspaceDirectoryError
from app.services.workspace.edits.workspace_edit_preview import (
    MAX_EDIT_TEXT_BYTES,
    EditPreviewError,
)
from app.services.workspace.files.workspace_file import WorkspaceFileError
from app.services.workspace.edits.workspace_file_preview import (
    preview_task_file_replacement,
)
from app.services.workspace.directory.workspace_path import WorkspacePathError
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError


class PreviewFileEditArguments(BaseModel):
    """模型只描述目标文件和替换内容，不提供身份或批准状态。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    relative_path: str = Field(
        min_length=1,
        max_length=4096,
        description=(
            "当前项目内的相对文件路径，使用正斜杠；"
            "不能使用绝对路径或上级引用"
        ),
    )
    old_text: str = Field(
        min_length=1,
        max_length=MAX_EDIT_TEXT_BYTES,
        description=(
            "原文件中需要替换的精确文本，必须非空且唯一匹配；"
            "保留空格与换行，最多256 KiB UTF-8，不支持模糊匹配"
        ),
    )
    new_text: str = Field(
        max_length=MAX_EDIT_TEXT_BYTES,
        description=(
            "替换后的文本，空字符串表示删除；"
            "保留空格与换行，最多256 KiB UTF-8"
        ),
    )

    @field_validator("old_text", "new_text")
    @classmethod
    def validate_edit_text(cls, value: str) -> str:
        # Field的max_length按字符计数；实际策略还要限制UTF-8字节数。
        # 不strip或归一化换行，否则可能改变替换位置和内容。
        if "\x00" in value:
            raise ValueError("替换文本不能包含NUL")

        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("替换文本必须是有效UTF-8文本") from None

        if len(encoded) > MAX_EDIT_TEXT_BYTES:
            raise ValueError("替换文本超过256 KiB UTF-8")

        return value


def preview_file_edit(
    *,
    context: ToolExecutionContext,
    relative_path: str,
    old_text: str,
    new_text: str,
) -> str:
    """返回仅供审阅的预览，已知失败映射为固定安全分类。"""

    try:
        # 身份由服务端提供；底层读取仍重新检查当前资源归属。
        # 调用本工具不代表用户批准修改，也不会触发写入。
        result = preview_task_file_replacement(
            user_id=context.user_id,
            workspace_id=context.workspace_id,
            task_id=context.task_id,
            relative_path=relative_path,
            old_text=old_text,
            new_text=new_text,
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
        allowed_codes = {
            "file_read_unsupported",
            "file_not_regular",
            "file_too_large",
            "file_not_utf8_text",
            "file_changed",
        }
        code = (
            error.code
            if error.code in allowed_codes
            else "file_unavailable"
        )
        raise SafeToolExecutionError(code) from None

    except EditPreviewError as error:
        # 只公开已知分类，不把异常正文或原始文本放入Observation。
        allowed_codes = {
            "invalid_edit_text",
            "edit_text_too_large",
            "empty_old_text",
            "edit_no_change",
            "edit_target_not_found",
            "edit_target_ambiguous",
            "edit_preview_too_many_lines",
        }
        code = (
            error.code
            if error.code in allowed_codes
            else "edit_preview_unavailable"
        )
        raise SafeToolExecutionError(code) from None

    # 显式挑选公开字段，不能直接asdict(result)：
    # 内部preview包含完整updated_content，不应自动回传模型。
    return json.dumps(
        {
            "status": "preview_only",
            "relative_path": result.relative_path,
            "baseline_sha256": result.baseline_sha256,
            "before_byte_count": result.preview.before_byte_count,
            "after_byte_count": result.preview.after_byte_count,
            "diff": result.preview.diff,
            "diff_truncated": result.preview.diff_truncated,
        },
        ensure_ascii=False,
    )
