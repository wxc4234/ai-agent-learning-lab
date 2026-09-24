"""补丁提案保存适配：仅生成待审批记录，不批准或修改文件。"""

import json

from pydantic import ValidationError

from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.directory.workspace_directory import WorkspaceDirectoryError
from app.services.workspace.directory.workspace_path import WorkspacePathError
from app.services.workspace.edits.workspace_edit_preview import EditPreviewError
from app.services.workspace.proposals.file_edit_proposal_service import (
    ProposalBindingChangedError,
    create_task_file_patch_proposal,
)
from app.services.workspace.edits.workspace_unified_patch import UnifiedPatchError
from app.services.workspace.files.workspace_file import WorkspaceFileError
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError
from app.tools.preview_file_patch import PATCH_ERROR_CODES, PreviewFilePatchArguments


class CreateFilePatchProposalArguments(PreviewFilePatchArguments):
    """复用补丁输入约束；身份、摘要、提案编号和批准状态均不可填写。"""


def create_file_patch_proposal(
    *, context: ToolExecutionContext, relative_path: str, patch: str,
) -> str:
    """先校验再保存；仅返回提交回执，不追加查询或自动重试。"""

    # 类型检查防止内部错接，不能替代服务端来源和底层数据库授权。
    if not isinstance(context, ToolExecutionContext):
        raise SafeToolExecutionError('workspace_not_accessible')
    try:
        arguments = CreateFilePatchProposalArguments(relative_path=relative_path, patch=patch)
    except ValidationError:
        raise SafeToolExecutionError('patch_request_rejected') from None

    try:
        # 服务独占事务；任何未知异常都可能发生在提交确认之后。
        result = create_task_file_patch_proposal(
            user_id=context.user_id, workspace_id=context.workspace_id,
            task_id=context.task_id, **arguments.model_dump(),
        )
    except WorkspaceNotAccessibleError:
        raise SafeToolExecutionError('workspace_not_accessible') from None
    except ProposalBindingChangedError:
        raise SafeToolExecutionError('proposal_binding_changed') from None
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
        raise SafeToolExecutionError('proposal_save_unconfirmed') from None

    try:
        # 状态来自已提交回执，不能硬编码 pending 掩盖服务协议异常。
        # 显式投影避免候选正文、宿主目录及未来内部字段进入模型上下文。
        if result.status != 'pending':
            raise ValueError('unexpected creation status')
        return json.dumps({
            'proposal_id': result.proposal_id,
            'status': result.status,
            'relative_path': result.relative_path,
            'baseline_sha256': result.baseline_sha256,
            'proposed_sha256': result.proposed_sha256,
            'diff_truncated': result.diff_truncated,
            'created_at': result.created_at.isoformat(),
        }, ensure_ascii=False)
    except Exception:  # noqa: BLE001 -- 保存后投影失败不代表事务未提交。
        raise SafeToolExecutionError('proposal_save_unconfirmed') from None
