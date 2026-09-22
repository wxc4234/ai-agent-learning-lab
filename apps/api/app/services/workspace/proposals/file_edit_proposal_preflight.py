"""已批准提案的只读应用前核对；结果不是可缓存的写入授权。"""

from dataclasses import dataclass
from hashlib import sha256
import re

from app.database import SessionLocal
from app.repositories.workspace.file_edit_proposal_repository import (
    read_owned_proposal_application_source,
)
from app.services.workspace.proposals.file_edit_proposal_service import ProposalBindingChangedError
from app.services.workspace.files.workspace_file import MAX_TEXT_FILE_BYTES, read_task_text_file


class ProposalPreflightError(ValueError):
    """核对失败只使用固定文案，不暴露路径、正文或底层异常。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class FileEditProposalPreflight:
    """某次读取时的核对结果；不携带待写正文、绝对路径或文件句柄。"""

    proposal_id: str
    workspace_id: str
    task_id: str
    relative_path: str
    baseline_sha256: str
    proposed_sha256: str
    current_byte_count: int
    proposed_byte_count: int


def check_task_file_edit_proposal(
    *, user_id: int, workspace_id: str, task_id: str, proposal_id: str,
    expected_bound_root: str | None = None,
    expected_relative_path: str | None = None,
) -> FileEditProposalPreflight:
    """重新授权、核对保存内容及文件基线，不改变审批状态或文件。"""

    identity = {"user_id": user_id, "workspace_id": workspace_id,
                "task_id": task_id, "proposal_id": proposal_id}
    with SessionLocal() as session:
        source = dict(read_owned_proposal_application_source(session, **identity))

    if (
        (expected_bound_root is not None and source["bound_root"] != expected_bound_root)
        or (expected_relative_path is not None and source["relative_path"] != expected_relative_path)
    ):
        raise ProposalBindingChangedError()

    # 数据库查询事务已经关闭，后续文件I/O不占用事务或行锁。
    if source["status"] != "approved":
        raise ProposalPreflightError("proposal_not_approved", "提案尚未批准，不能核对应用")
    if source["current_root"] is None or source["current_root"] != source["bound_root"]:
        raise ProposalBindingChangedError()
    if source["diff_truncated"]:
        raise ProposalPreflightError("proposal_diff_incomplete", "提案Diff不完整，不能核对应用")

    # 不信任仅有approved标记的数据：验证完整待写内容与保存摘要一致。
    content = source["proposed_content"]
    valid = isinstance(content, str) and "\x00" not in content and len(content) <= MAX_TEXT_FILE_BYTES
    try:
        encoded = content.encode("utf-8") if valid else b""
    except UnicodeEncodeError:
        valid = False
        encoded = b""
    if (
        not valid or len(encoded) > MAX_TEXT_FILE_BYTES
        or any(not isinstance(source[key], str) or re.fullmatch(r"[0-9a-f]{64}", source[key]) is None
               for key in ("baseline_sha256", "proposed_sha256"))
        or sha256(encoded).hexdigest() != source["proposed_sha256"]
    ):
        raise ProposalPreflightError("proposal_content_invalid", "提案内容校验失败，请重新生成提案")

    current = read_task_text_file(
        user_id=user_id, workspace_id=workspace_id, task_id=task_id,
        relative_path=source["relative_path"], expected_bound_root=source["bound_root"],
    )
    # 严格UTF-8往返保留BOM/CRLF，不能用归一化后的文本比较基线。
    if sha256(current.content.encode("utf-8")).hexdigest() != source["baseline_sha256"]:
        raise ProposalPreflightError("proposal_baseline_changed", "文件内容已变化，请重新生成提案")

    # 文件句柄已关闭。新查询再次授权并拒绝可观察到的元数据变化。
    # 这不检测ABA，也不消除返回后文件或权限变化；真正写入必须另行检查。
    with SessionLocal() as session:
        latest = dict(read_owned_proposal_application_source(session, **identity))
    if latest != source:
        raise ProposalPreflightError("proposal_changed", "核对期间提案或目录绑定已变化，请重新核对")

    return FileEditProposalPreflight(
        proposal_id=proposal_id, workspace_id=workspace_id, task_id=task_id,
        relative_path=current.relative_path, baseline_sha256=source["baseline_sha256"],
        proposed_sha256=source["proposed_sha256"], current_byte_count=current.byte_count,
        proposed_byte_count=len(encoded),
    )
