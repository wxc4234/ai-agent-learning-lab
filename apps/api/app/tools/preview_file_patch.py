"""补丁预览的工具适配；由请求上下文决定能力可见性，不执行写入。"""

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.directory.workspace_directory import WorkspaceDirectoryError
from app.services.workspace.directory.workspace_path import WorkspacePathError
from app.services.workspace.edits.workspace_edit_preview import EditPreviewError
from app.services.workspace.edits.workspace_patch_preview import preview_task_file_patch
from app.services.workspace.edits.workspace_unified_patch import MAX_PATCH_BYTES, MAX_PATH_BYTES, UnifiedPatchError
from app.services.workspace.files.workspace_file import WorkspaceFileError
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError


PATCH_ERROR_CODES = frozenset({
    'patch_invalid_text', 'patch_limit_exceeded', 'patch_invalid_path',
    'patch_invalid_format', 'patch_target_mismatch', 'patch_hunk_count_mismatch',
    'patch_hunks_overlap', 'patch_range_out_of_bounds', 'patch_new_position_mismatch',
    'patch_context_mismatch', 'patch_no_change',
})


class PreviewFilePatchArguments(BaseModel):
    """模型仅描述单文件 LF 补丁，不提供身份、基线或批准状态。"""

    model_config = ConfigDict(extra='forbid', strict=True)

    relative_path: str = Field(
        min_length=1, max_length=MAX_PATH_BYTES,
        description='当前项目内的相对路径；补丁 a/b 文件头必须匹配规范路径，最多1024字节UTF-8',
    )
    patch: str = Field(
        min_length=1, max_length=MAX_PATCH_BYTES,
        description='单文件统一Diff，仅支持LF修改，最多512 KiB UTF-8；不支持Git扩展头、文件新增/删除或重命名',
    )

    @field_validator('relative_path', 'patch')
    @classmethod
    def validate_text(cls, value: str, info) -> str:
        # 字符上限不能替代 UTF-8 字节上限；不 trim 或转换换行。
        if '\x00' in value or '\r' in value:
            raise ValueError('只接受不含NUL或CR的文本')
        try:
            size = len(value.encode('utf-8'))
        except UnicodeEncodeError:
            raise ValueError('必须是有效UTF-8文本') from None
        limit = MAX_PATCH_BYTES if info.field_name == 'patch' else MAX_PATH_BYTES
        if size > limit:
            raise ValueError('输入超过UTF-8字节上限')
        return value


def preview_file_patch(
    *, context: ToolExecutionContext, relative_path: str, patch: str,
) -> str:
    """校验先于授权读取；只公开审阅信息，取消继续向上传播。"""

    # 类型检查防止内部错接，不能替代服务端来源和底层数据库授权。
    if not isinstance(context, ToolExecutionContext):
        raise SafeToolExecutionError('workspace_not_accessible')
    try:
        arguments = PreviewFilePatchArguments(relative_path=relative_path, patch=patch)
    except ValidationError:
        raise SafeToolExecutionError('patch_request_rejected') from None

    try:
        result = preview_task_file_patch(
            user_id=context.user_id, workspace_id=context.workspace_id,
            task_id=context.task_id, **arguments.model_dump(),
        )
    except WorkspaceNotAccessibleError:
        raise SafeToolExecutionError('workspace_not_accessible') from None
    except WorkspaceDirectoryError:
        raise SafeToolExecutionError('workspace_directory_unavailable') from None
    except WorkspacePathError as error:
        code = 'workspace_directory_unbound' if error.code == 'workspace_directory_unbound' else 'workspace_path_rejected'
        raise SafeToolExecutionError(code) from None
    except WorkspaceFileError as error:
        allowed = {'file_read_unsupported', 'file_not_regular', 'file_too_large', 'file_not_utf8_text', 'file_changed'}
        code = error.code if error.code in allowed else 'file_unavailable'
        raise SafeToolExecutionError(code) from None
    except UnifiedPatchError as error:
        code = error.code if error.code in PATCH_ERROR_CODES else 'patch_preview_unavailable'
        raise SafeToolExecutionError(code) from None
    except EditPreviewError as error:
        allowed = {'invalid_edit_text', 'edit_text_too_large', 'edit_preview_too_many_lines'}
        code = error.code if error.code in allowed else 'patch_preview_unavailable'
        raise SafeToolExecutionError(code) from None
    except Exception:  # noqa: BLE001 -- 未知普通错误不回显正文；CancelledError不被捕获。
        raise SafeToolExecutionError('patch_preview_unavailable') from None

    try:
        # 不能 asdict：完整候选仅保留在内部服务，不自动进入模型上下文。
        # Diff 本身包含文件片段；字段投影不代表对正文做敏感信息扫描。
        return json.dumps({
            'status': 'preview_only',
            'relative_path': result.relative_path,
            'baseline_sha256': result.baseline_sha256,
            'before_byte_count': result.preview.before_byte_count,
            'after_byte_count': result.preview.after_byte_count,
            'diff': result.preview.diff,
            'diff_truncated': result.preview.diff_truncated,
        }, ensure_ascii=False)
    except Exception:  # noqa: BLE001 -- 投影/序列化失败不得伪装成成功预览。
        raise SafeToolExecutionError('patch_preview_unavailable') from None
