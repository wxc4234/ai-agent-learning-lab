"""读取已授权文件并生成编辑预览，不写文件或保存审批状态。"""

from dataclasses import dataclass
from hashlib import sha256

from app.services.workspace.edits.workspace_edit_preview import (
    TextEditPreview,
    preview_text_replacement,
)
from app.services.workspace.files.workspace_file import read_task_text_file


@dataclass(frozen=True)
class WorkspaceFileEditPreview:
    """相对路径、原文内容基线与完整预览，不携带宿主绝对路径。"""

    relative_path: str

    # 摘要对应本次读取的原始UTF-8字节，不是修改后的内容。
    # 仅用于后续内容冲突检测，不授予写入权限。
    baseline_sha256: str

    # 复用上一课结果，避免重复维护内容、字节数和Diff字段。
    preview: TextEditPreview


def preview_task_file_replacement(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    relative_path: str,
    old_text: str,
    new_text: str,
) -> WorkspaceFileEditPreview:
    """先授权读取，再对同一份内存内容生成摘要与替换预览。"""

    # 身份与任务定位必须来自服务端可信上下文。
    # 读取服务负责重新授权、路径边界、普通文件及大小/编码检查。
    source = read_task_text_file(
        user_id=user_id,
        workspace_id=workspace_id,
        task_id=task_id,
        relative_path=relative_path,
    )

    # 读取返回时数据库Session及文件描述符均已关闭。
    # 后续只处理内存，不跨Diff计算持有数据库事务或文件句柄。
    preview = preview_text_replacement(
        content=source.content,
        old_text=old_text,
        new_text=new_text,
    )

    # 读取服务使用严格UTF-8解码，没有换行或Unicode归一化。
    # 因此重新编码可还原本次读到的字节，包括BOM和原始换行。
    # 不重新打开文件，否则摘要可能对应另一时刻的内容。
    baseline_sha256 = sha256(
        source.content.encode("utf-8")
    ).hexdigest()

    return WorkspaceFileEditPreview(
        # 使用读取服务返回的规范相对路径，而非直接回显输入。
        relative_path=source.relative_path,
        baseline_sha256=baseline_sha256,
        preview=preview,
    )
