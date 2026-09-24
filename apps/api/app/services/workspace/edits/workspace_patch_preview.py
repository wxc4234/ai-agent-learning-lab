"""授权读取一次并预览单文件补丁；不保存提案或执行文件写入。"""

from hashlib import sha256

from app.services.workspace.edits.workspace_edit_preview import (
    TextEditPreview,
    _build_review_diff,
)
from app.services.workspace.edits.workspace_file_preview import WorkspaceFileEditPreview
from app.services.workspace.edits.workspace_unified_patch import preview_unified_patch
from app.services.workspace.files.workspace_file import read_task_text_file


def preview_task_file_patch(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    relative_path: str,
    patch: str,
) -> WorkspaceFileEditPreview:
    """从同一次授权读取生成原文基线、完整候选及有限审阅数据。"""

    # 身份和 Task 归属来自可信调用方；读取服务在真实边界重新授权。
    source = read_task_text_file(
        user_id=user_id,
        workspace_id=workspace_id,
        task_id=task_id,
        relative_path=relative_path,
    )

    # 返回时 Session 和文件描述符均已关闭；后续计算不持有外部事务。
    # 补丁文件头必须匹配读取服务返回的规范相对路径，不接受另一目标。
    candidate = preview_unified_patch(
        content=source.content,
        relative_path=source.relative_path,
        patch=patch,
    )

    # 同领域复用既有审阅表示和截断规则，不把输入补丁当作最终审阅结果。
    # 截断只影响展示，完整候选仍单独保留，不能用截断 Diff 执行应用。
    diff, truncated = _build_review_diff(
        before=source.content,
        after=candidate.updated_content,
    )
    preview = TextEditPreview(
        updated_content=candidate.updated_content,
        before_byte_count=candidate.before_byte_count,
        after_byte_count=candidate.after_byte_count,
        diff=diff,
        diff_truncated=truncated,
    )

    # 严格 UTF-8 读取没有归一化，可还原本次读取的字节；禁止重新打开文件。
    # 基线不是写入许可，也不保证预览返回时磁盘内容仍然相同。
    return WorkspaceFileEditPreview(
        relative_path=source.relative_path,
        baseline_sha256=sha256(source.content.encode('utf-8')).hexdigest(),
        preview=preview,
    )
