"""内部许可发放、查询和显式撤销；不进行项目写入，也不恢复应用机会。"""

from hashlib import sha256
import re
from typing import TypedDict
from uuid import uuid4

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import ProjectWriteGrantRecord
from app.repositories.workspace.file_edit_proposal_repository import (
    lock_file_edit_proposal_for_decision,
    lock_owned_proposal_task,
    read_owned_proposal_application_source,
)
from app.repositories.workspace.project_write_grant_repository import find_project_write_grant
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.files.workspace_file_replace import _validate_content
from app.services.workspace.proposals.project_write_policy import (
    ProjectWriteDecision, ProjectWriteFacts, ProjectWriteGrant, ProjectWriteTarget, evaluate_project_write_policy,
)
from app.services.workspace.proposals.project_write_snapshot import ProjectWriteSnapshotReader


class GrantScope(TypedDict):
    user_id: int
    workspace_id: str
    task_id: str
    proposal_id: str


class ProjectWriteAssessmentError(ValueError):
    def __init__(self) -> None:
        # 无法完成观察时保留未知；不伪装成许可缺失或目标未变化。
        super().__init__('project_write_assessment_unavailable')


class ProjectWriteGrantError(ValueError):
    def __init__(self) -> None:
        # 包括提交结果未知；只允许重新查询，不在这里重放任何写操作。
        super().__init__('project_write_grant_unavailable')


class ProjectWriteGrantConflictError(ProjectWriteGrantError):
    """持锁确认的已知冲突；未执行本次变更，区别于提交结果未知。"""


def _authorize(session: Session, scope: GrantScope) -> dict:
    if type(scope['user_id']) is not int or scope['user_id'] <= 0:
        raise ProjectWriteGrantError()
    _, task = lock_owned_proposal_task(
        session, user_id=scope['user_id'], workspace_id=scope['workspace_id'], task_id=scope['task_id'],
    )
    lock_file_edit_proposal_for_decision(session, task_id=task.id, proposal_id=scope['proposal_id'])
    return dict(read_owned_proposal_application_source(session, **scope))


def _decode(record: ProjectWriteGrantRecord, source: dict, user_id: int) -> ProjectWriteGrant:
    grant = ProjectWriteGrant(
        grant_id=record.grant_id, revision=record.revision,
        enabled=record.enabled, target=ProjectWriteTarget.model_validate(record.target),
    )
    # JSON不是可信输入；至少核对不可跨资源借用的内部归属。
    if any(getattr(grant.target, field) != value for field, value in (
        ('user_id', user_id), ('workspace_id', source['workspace_pk']),
        ('task_id', source['task_pk']), ('proposal_id', source['proposal_pk']),
        ('conversation_id', source['conversation_pk']),
    )) or (grant.enabled, grant.revision) not in ((True, 1), (False, 2)):
        raise ProjectWriteGrantError()
    return grant


class ProjectWriteGrantService:
    """宿主长期持有，内部构造可信读取器；不接收调用方自报的目标。

    查询仅返回历史许可事实；执行时仍需新快照、策略核对和排他条件。
    一个提案仅一次发放。撤销/进程重启后需新提案和新的显式许可。
    """

    def __init__(self) -> None:
        self._reader = ProjectWriteSnapshotReader()

    def issue(self, *, user_id: int, workspace_id: str, task_id: str, proposal_id: str) -> ProjectWriteGrant:
        scope: GrantScope = {'user_id': user_id, 'workspace_id': workspace_id, 'task_id': task_id, 'proposal_id': proposal_id}
        try:
            # 文件I/O全部在写事务前结束；这不是跨数据库/文件系统原子快照。
            target = self._reader.read(**scope)
            with SessionLocal.begin() as session:
                source = _authorize(session, scope)
                if find_project_write_grant(session, source['proposal_pk']) is not None:
                    raise ProjectWriteGrantConflictError()
                if (source['status'] != 'approved' or source['application_status'] != 'idle'
                        or source['diff_truncated'] or not source['current_root']
                        or source['current_root'] != source['bound_root']):
                    raise ProjectWriteGrantError()
                _validate_content(source['proposed_content'], source['proposed_sha256'])
                expected = {
                    'user_id': user_id, 'workspace_id': source['workspace_pk'], 'task_id': source['task_pk'],
                    'conversation_id': source['conversation_pk'], 'proposal_id': source['proposal_pk'],
                    'binding_revision': source['binding_revision'], 'relative_path': source['relative_path'],
                    'baseline_sha256': source['baseline_sha256'], 'proposed_sha256': source['proposed_sha256'],
                }
                if any(getattr(target, key) != value for key, value in expected.items()):
                    raise ProjectWriteGrantError()
                result = ProjectWriteGrant(grant_id=uuid4().hex, revision=1, enabled=True, target=target)
                session.add(ProjectWriteGrantRecord(
                    grant_id=result.grant_id, proposal_id=target.proposal_id,
                    target=target.model_dump(mode='json'), revision=1, enabled=True,
                ))
                session.flush()
            # 只有确认提交成功后才返回；唯一约束和提案锁阻止重复发放。
            return result
        except (WorkspaceNotAccessibleError, ProjectWriteGrantConflictError):
            raise
        except Exception:  # noqa: BLE001 -- 未知DB/提交失败统一拒绝，不捕获中断或自动重试。
            raise ProjectWriteGrantError() from None

    def read(self, *, user_id: int, workspace_id: str, task_id: str, proposal_id: str) -> ProjectWriteGrant | None:
        scope: GrantScope = {'user_id': user_id, 'workspace_id': workspace_id, 'task_id': task_id, 'proposal_id': proposal_id}
        try:
            with SessionLocal.begin() as session:
                source = _authorize(session, scope)
                record = find_project_write_grant(session, source['proposal_pk'])
                result = None if record is None else _decode(record, source, user_id)
            return result
        except (WorkspaceNotAccessibleError, ProjectWriteGrantConflictError):
            raise
        except Exception:  # noqa: BLE001 -- 未知DB/提交失败统一拒绝，不捕获中断或自动重试。
            raise ProjectWriteGrantError() from None

    def revoke(self, *, user_id: int, workspace_id: str, task_id: str, proposal_id: str,
               grant_id: str, revision: int) -> ProjectWriteGrant:
        scope: GrantScope = {'user_id': user_id, 'workspace_id': workspace_id, 'task_id': task_id, 'proposal_id': proposal_id}
        try:
            with SessionLocal.begin() as session:
                source = _authorize(session, scope)
                record = find_project_write_grant(session, source['proposal_pk'])
                # 撤销不要求文件仍存在或提案仍idle；允许撤销已经失效的许可。
                if (record is None or type(revision) is not int or type(grant_id) is not str
                        or record.grant_id != grant_id or record.revision != revision or not record.enabled):
                    raise ProjectWriteGrantConflictError()
                record.enabled = False
                record.revision += 1
                session.flush()
                result = _decode(record, source, user_id)
            return result
        except (WorkspaceNotAccessibleError, ProjectWriteGrantConflictError):
            raise
        except Exception:  # noqa: BLE001 -- 未知DB/提交失败统一拒绝，不捕获中断或自动重试。
            raise ProjectWriteGrantError() from None


    def assess(self, *, user_id: int, workspace_id: str, task_id: str, proposal_id: str,
               grant_id: str, revision: int, apply_requested: bool) -> ProjectWriteDecision:
        """只读内部判定；参数只表达定位和本次意图，不能充当可信事实。

        两次持锁数据库读取夹住一次无事务文件观察。返回前的重查可发现
        已提交的撤销/占用，但返回后仍可变化，判定绝非执行或恢复凭据。
        """
        scope: GrantScope = {'user_id': user_id, 'workspace_id': workspace_id, 'task_id': task_id, 'proposal_id': proposal_id}
        try:
            # bool不是合法修订号；不允许字符串/整数真值隐式变成应用意图。
            if (type(apply_requested) is not bool or type(revision) is not int or revision <= 0
                    or type(grant_id) is not str or re.fullmatch(r'[0-9a-f]{32}', grant_id) is None):
                return ProjectWriteDecision('invalid_facts')
            with SessionLocal.begin() as session:
                source = _authorize(session, scope)
                record = find_project_write_grant(session, source['proposal_pk'])
                confirmed = None if record is None else _decode(record, source, user_id)
            # 先授权再给状态分类，且已知拒绝不需要打开用户文件。
            if not apply_requested:
                return ProjectWriteDecision('apply_not_requested')
            if confirmed is None:
                return ProjectWriteDecision('grant_missing')
            if not confirmed.enabled:
                return ProjectWriteDecision('grant_revoked')
            if confirmed.grant_id != grant_id or confirmed.revision != revision:
                return ProjectWriteDecision('grant_changed')
            if source['status'] != 'approved':
                return ProjectWriteDecision('proposal_not_approved')
            if source['application_status'] != 'idle':
                return ProjectWriteDecision('application_not_idle')
            if source['diff_truncated']:
                return ProjectWriteDecision('diff_incomplete')

            # 与issue共用实例身份，不能另建reader或从持久许可恢复runtime_id。
            # reader成功意味着支持平台、目录对象/元数据/原字节摘要均核对完成。
            observed = self._reader.read(**scope)
            with SessionLocal.begin() as session:
                latest = _authorize(session, scope)
                record = find_project_write_grant(session, latest['proposal_pk'])
                current = None if record is None else _decode(record, latest, user_id)
            # Session已关闭；候选摘要在内存重算，不将声明摘要直接当观察证据。
            # 任一提案事实变化都不拼接成部分新、部分旧的“成功”事实。
            if latest != source:
                raise ProjectWriteAssessmentError()
            candidate = _validate_content(latest['proposed_content'], latest['proposed_sha256'])
            facts = ProjectWriteFacts(
                target=confirmed.target, observed_target=observed,
                confirmed_grant=confirmed, current_grant=current,
                authorized=True, apply_requested=apply_requested,
                proposal_status=latest['status'], application_status=latest['application_status'],
                diff_complete=not latest['diff_truncated'],
                # observed的基线已被reader从原始文件字节计算并匹配，非裸DB声明。
                current_sha256=observed.baseline_sha256, candidate_sha256=sha256(candidate).hexdigest(),
                filesystem_checked=True, platform_supported=True,
                # 现有观察器不提供排他访问；服务没有任何参数可将其开启。
                exclusive_access_confirmed=False,
            )
            return evaluate_project_write_policy(facts)
        except (WorkspaceNotAccessibleError, ProjectWriteGrantConflictError):
            raise
        except Exception:  # noqa: BLE001 -- DB/文件未知失败保留未知，不重试；中断继续传播。
            raise ProjectWriteAssessmentError() from None
