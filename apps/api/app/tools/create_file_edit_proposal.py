"""将提案保存服务适配为工具，不批准或应用文件修改。"""

import json

from sqlalchemy.exc import SQLAlchemyError

from app.repositories.workspace.workspace_repository import (
    WorkspaceNotAccessibleError,
)
from app.services.workspace.file_edit_proposal_service import (
    ProposalBindingChangedError,
    create_task_file_edit_proposal,
)
from app.services.workspace.workspace_directory import WorkspaceDirectoryError
from app.services.workspace.workspace_edit_preview import EditPreviewError
from app.services.workspace.workspace_file import WorkspaceFileError
from app.services.workspace.workspace_path import WorkspacePathError
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError
from app.tools.preview_file_edit import PreviewFileEditArguments


class CreateFileEditProposalArguments(PreviewFileEditArguments):
    """创建与预览使用相同的替换参数，但拥有独立的工具参数类型。"""

    # 继承严格类型、额外字段拒绝及 UTF-8 字节校验。
    # 身份、提案编号、摘要和批准状态都不能由模型填写。


def create_file_edit_proposal(
    *,
    context: ToolExecutionContext,
    relative_path: str,
    old_text: str,
    new_text: str,
) -> str:
    """保存一次待审批提案，返回提交成功后的公开定位信息。"""

    try:
        # 身份来自可信上下文；服务仍会在保存前重新授权。
        # 事务完全由服务管理，工具不自行提交，也不重试保存。
        result = create_task_file_edit_proposal(
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

    except ProposalBindingChangedError:
        raise SafeToolExecutionError(
            "proposal_binding_changed",
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

    except SQLAlchemyError:
        # 数据库异常可能发生在提交确认阶段，不能断言提案一定没保存。
        # 不向模型暴露 SQL、连接信息或底层异常正文。
        raise SafeToolExecutionError(
            "proposal_save_unconfirmed",
        ) from None

    # 直接使用保存服务返回的提交结果，不额外查询或重新生成预览。
    # 创建成功后再查询若失败，会把“已保存”混成整个操作失败。
    return json.dumps(
        {
            "proposal_id": result.proposal_id,
            "status": result.status,
            "relative_path": result.relative_path,
            "baseline_sha256": result.baseline_sha256,
            "proposed_sha256": result.proposed_sha256,
            "diff_truncated": result.diff_truncated,
            "created_at": result.created_at.isoformat(),
        },
        ensure_ascii=False,
    )
