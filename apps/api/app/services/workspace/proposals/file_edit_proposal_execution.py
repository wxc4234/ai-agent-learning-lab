"""临时样例的提案应用装配；尚未开放为HTTP或模型工具。"""

from dataclasses import dataclass
from typing import Literal

from app.database import SessionLocal
from app.repositories.workspace.file_edit_proposal_repository import (
    lock_file_edit_proposal_for_decision,
    lock_owned_proposal_task,
)
from app.services.workspace.proposals.file_edit_proposal_application import (
    ApplicationOutcome,
    ProposalApplicationError,
    claim_task_file_edit_proposal,
    finish_task_file_edit_proposal,
)
from app.services.workspace.proposals.file_edit_proposal_preflight import (
    check_task_file_edit_proposal,
)
from app.services.workspace.proposals.file_edit_proposal_service import (
    ProposalBindingChangedError,
)
from app.services.workspace.files.workspace_file_replace import (
    FileReplaceResult,
    replace_workspace_text_file,
)


FileStatus = Literal[
    "not_attempted",
    "not_replaced",
    "replaced",
    "uncertain",
]

RecordedStatus = Literal[
    "applied",
    "not_applied",
    "uncertain",
    "unknown",
]


@dataclass(frozen=True)
class ProposalExecutionResult:
    """不携带正文、宿主路径或执行令牌的内部回执。"""

    proposal_id: str
    file_status: FileStatus

    # unknown表示未确认数据库终态，不是数据库中的新状态值。
    application_status: RecordedStatus
    code: str

    # 尚未调用文件原语或原语异常退出时，不能虚构清理成功。
    cleanup_complete: bool | None


@dataclass(frozen=True, repr=False)
class _ExecutionSource:
    """只在本次调用中使用，不缓存、不打印、不作为公开响应。"""

    bound_root: str
    relative_path: str
    baseline_sha256: str
    proposed_sha256: str
    proposed_content: str


def _read_claimed_source(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    proposal_id: str,
    application_token: str,
) -> _ExecutionSource:
    """在固定锁序内重新授权，并确认当前调用仍持有执行占用。"""

    # 此事务只读取数据库。
    # 返回前释放所有行锁，不在事务中读取或替换项目文件。
    with SessionLocal.begin() as session:
        workspace, task = lock_owned_proposal_task(
            session,
            user_id=user_id,
            workspace_id=workspace_id,
            task_id=task_id,
        )
        proposal = lock_file_edit_proposal_for_decision(
            session,
            task_id=task.id,
            proposal_id=proposal_id,
        )

        if (
            proposal.application_status != "running"
            or proposal.application_token != application_token
        ):
            raise ProposalApplicationError(
                "proposal_application_conflict",
                "应用执行状态或凭据不匹配",
            )

        if proposal.status != "approved" or proposal.diff_truncated:
            raise ProposalApplicationError(
                "proposal_not_approved",
                "提案未满足批准条件",
            )

        if (
            workspace.root_path is None
            or workspace.root_path != proposal.bound_root
        ):
            raise ProposalBindingChangedError()

        # 复制必要字段；不把ORM对象或Session带入文件操作阶段。
        source = _ExecutionSource(
            bound_root=proposal.bound_root,
            relative_path=proposal.relative_path,
            baseline_sha256=proposal.baseline_sha256,
            proposed_sha256=proposal.proposed_sha256,
            proposed_content=proposal.proposed_content,
        )

    return source


def execute_task_file_edit_proposal(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    proposal_id: str,
    expected_bound_root: str | None = None,
    expected_relative_path: str | None = None,
    expected_parent_identity: tuple[int, int] | None = None,
) -> ProposalExecutionResult:
    """领取一次、核对一次、尝试替换一次、登记一次；不自动重试。"""

    identity = {
        "user_id": user_id,
        "workspace_id": workspace_id,
        "task_id": task_id,
        "proposal_id": proposal_id,
    }

    try:
        claim = claim_task_file_edit_proposal(**identity)
    except Exception:  # noqa: BLE001 -- 领取提交或回执失败可能意味着占用已经成立
        # 没收到令牌就绝不操作文件，也不能猜测数据库仍为idle。
        # 不尝试补领、释放或登记，因为本调用没有确认取得执行权。
        return ProposalExecutionResult(
            proposal_id=proposal_id,
            file_status="not_attempted",
            application_status="unknown",
            code="proposal_application_claim_unconfirmed",
            cleanup_complete=None,
        )

    file_status: FileStatus = "not_attempted"
    cleanup_complete: bool | None = None
    outcome: ApplicationOutcome = "not_applied"

    # 该标记表示已进入可能产生文件副作用的调用边界。
    # 不能等调用成功返回后才设置。
    replacement_entered = False

    try:
        source = _read_claimed_source(
            **identity,
            application_token=claim.application_token,
        )

        if (
            (expected_bound_root is not None and source.bound_root != expected_bound_root)
            or (expected_relative_path is not None and source.relative_path != expected_relative_path)
        ):
            raise ProposalBindingChangedError()

        # 复用现有只读核对：完整内容摘要、当前字节基线及重新授权。
        # 核对结果不是可缓存的写入许可证。
        if expected_bound_root is None and expected_relative_path is None:
            check_task_file_edit_proposal(**identity)
        else:
            check_task_file_edit_proposal(
                **identity, expected_bound_root=expected_bound_root,
                expected_relative_path=expected_relative_path,
            )

        latest = _read_claimed_source(
            **identity,
            application_token=claim.application_token,
        )
        if latest != source:
            raise ProposalApplicationError(
                "proposal_changed",
                "执行准备期间提案或绑定已变化",
            )

        # 文件系统边界：这里没有数据库事务或行锁。
        # 原语还会重新检查文件基线、路径对象和元数据。
        replacement_entered = True
        result = replace_workspace_text_file(
            bound_root=latest.bound_root,
            relative_path=latest.relative_path,
            baseline_sha256=latest.baseline_sha256,
            proposed_content=latest.proposed_content,
            proposed_sha256=latest.proposed_sha256,
            **({"expected_parent_identity": expected_parent_identity}
               if expected_parent_identity is not None else {}),
        )

        # 内部协议异常也不能解释成“未写入”。
        if (
            not isinstance(result, FileReplaceResult)
            or result.status not in (
                "not_replaced",
                "replaced",
                "uncertain",
            )
            or type(result.cleanup_complete) is not bool
        ):
            raise ProposalApplicationError(
                "proposal_application_result_invalid",
                "文件操作结果无法确认",
            )

        file_status = result.status
        cleanup_complete = result.cleanup_complete

        if result.status == "replaced" and result.cleanup_complete:
            outcome = "applied"
        elif result.status == "not_replaced" and result.cleanup_complete:
            outcome = "not_applied"
        else:
            # 即使目标结果已知，清理未完成也不能放开资源保护。
            # file_status仍保留目标文件的证据，不将两种事实混为一谈。
            outcome = "uncertain"

    except Exception:  # noqa: BLE001 -- 根据文件调用边界保守分类，不暴露底层异常
        if replacement_entered:
            file_status = "uncertain"
            outcome = "uncertain"
        else:
            # 本调用尚未进入文件替换阶段，可以确认没有由它执行替换。
            # 不代表外部编辑器没有修改文件。
            file_status = "not_attempted"
            outcome = "not_applied"

    # 故意不捕获BaseException：
    # KeyboardInterrupt等中断继续传播，保留running阻止重新领取。
    # 不能在不确定的中断现场自动释放占用或重新执行文件操作。

    try:
        finish_task_file_edit_proposal(
            **identity,
            application_token=claim.application_token,
            outcome=outcome,
        )
    except Exception:  # noqa: BLE001 -- 登记可能已提交，但回执未能确认
        # 保留文件证据，不回滚文件，不重试数据库终态登记。
        # 数据库可能仍running，也可能已经提交终态。
        return ProposalExecutionResult(
            proposal_id=proposal_id,
            file_status=file_status,
            application_status="unknown",
            code="proposal_application_registration_unconfirmed",
            cleanup_complete=cleanup_complete,
        )

    codes = {
        "applied": "proposal_application_applied",
        "not_applied": "proposal_application_not_applied",
        "uncertain": "proposal_application_uncertain",
    }
    return ProposalExecutionResult(
        proposal_id=proposal_id,
        file_status=file_status,
        application_status=outcome,
        code=codes[outcome],
        cleanup_complete=cleanup_complete,
    )
