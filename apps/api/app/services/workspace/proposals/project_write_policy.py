"""普通项目单文件应用的只读策略契约；执行入口仍独立重新授权与占用。

所有事实必须由可信宿主重新授权和观察，不接受浏览器/模型提供的许可对象。
本模块不读数据库/文件、不领取执行占用、不签发凭据，也不保存许可或撤销状态。
"""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.services.workspace.directory.workspace_path import _parse_relative_path

PositiveId = Annotated[int, Field(gt=0)]
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Identifier = Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]


class _Snapshot(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, revalidate_instances='always')


class FileObjectIdentity(_Snapshot):
    """设备/对象编号只是本次观察值，不是文件句柄或跨主机身份。"""

    device: Annotated[int, Field(ge=0)]
    inode: PositiveId


class ProjectWriteTarget(_Snapshot):
    # 内部主键防止同一外部编号重建后继承许可；宿主每次启动使用新的runtime_id。
    runtime_id: Identifier
    user_id: PositiveId
    workspace_id: PositiveId
    task_id: PositiveId
    conversation_id: PositiveId
    proposal_id: PositiveId
    # 绑定修订由绑定仓库在事务中单调维护，路径改回原值也不能复用旧许可。
    binding_revision: PositiveId
    root_identity: FileObjectIdentity
    file_identity: FileObjectIdentity
    relative_path: Annotated[str, Field(min_length=1, max_length=4096)]
    baseline_sha256: Digest
    proposed_sha256: Digest

    @field_validator('relative_path')
    @classmethod
    def canonical_path(cls, value: str) -> str:
        # 复用既有纯语法规则；不resolve、不折叠路径、不把路径校验视为授权。
        parsed = _parse_relative_path(value)
        if value != parsed.as_posix() or not parsed.parts:
            raise ValueError('noncanonical_relative_path')
        return value


class ProjectWriteGrant(_Snapshot):
    """显式项目写入许可的快照，与提案approved状态独立。"""

    grant_id: Identifier
    revision: PositiveId
    target: ProjectWriteTarget
    enabled: bool = False


class ProjectWriteFacts(_Snapshot):
    # target是用户确认的具体提案；observed_target来自本次重新授权/目录对象核对。
    target: ProjectWriteTarget
    observed_target: ProjectWriteTarget | None = None
    confirmed_grant: ProjectWriteGrant | None = None
    current_grant: ProjectWriteGrant | None = None
    authorized: bool = False
    apply_requested: bool = False
    proposal_status: Literal['pending', 'approved', 'rejected', 'unknown'] = 'unknown'
    application_status: Literal['idle', 'running', 'applied', 'not_applied', 'uncertain', 'unknown'] = 'unknown'
    diff_complete: bool = False
    # 两个摘要须由宿主重新读取原始字节计算，不能复制数据库的声明摘要冒充观察值。
    current_sha256: Digest | None = None
    candidate_sha256: Digest | None = None
    filesystem_checked: bool = False
    platform_supported: bool = False
    # 采用乐观版本检测；内部串行与执行占用由真实执行器提供，不由调用者声明。


PolicyCode = Literal[
    'invalid_facts', 'not_authorized', 'apply_not_requested', 'grant_missing',
    'grant_revoked', 'grant_changed', 'target_changed', 'proposal_not_approved',
    'application_not_idle', 'diff_incomplete', 'baseline_changed', 'candidate_changed',
    'filesystem_unconfirmed', 'platform_unsupported', 'exclusive_access_unconfirmed', 'eligible',
]


@dataclass(frozen=True, slots=True)
class ProjectWriteDecision:
    # 只返回固定分类，不携带宿主路径、内容、令牌或执行器参数。
    code: PolicyCode

    @property
    def eligible(self) -> bool:
        """仅表示此组事实满足必要条件，不能缓存为写入授权或恢复重试权。"""
        return self.code == 'eligible'


def evaluate_project_write_policy(facts: ProjectWriteFacts) -> ProjectWriteDecision:
    """无副作用判定；执行接入后仍须在真实边界重新授权、占用并核对文件。

    本函数不跨数据库/文件系统提供原子性。撤销、占用和目录修订的持久化，
    以及观察后发生的并发变化，都不能由一次纯函数结果解决。
    """
    if type(facts) is not ProjectWriteFacts:
        return ProjectWriteDecision('invalid_facts')
    try:
        # frozen不能阻止model_copy/model_construct绕过验证；边界再验证嵌套快照。
        value = ProjectWriteFacts.model_validate(facts)
    except ValidationError:
        return ProjectWriteDecision('invalid_facts')
    if not value.authorized:
        return ProjectWriteDecision('not_authorized')
    if not value.apply_requested:
        return ProjectWriteDecision('apply_not_requested')
    confirmed, current = value.confirmed_grant, value.current_grant
    if confirmed is None or current is None:
        return ProjectWriteDecision('grant_missing')
    if not confirmed.enabled or not current.enabled:
        return ProjectWriteDecision('grant_revoked')
    if confirmed != current:
        return ProjectWriteDecision('grant_changed')
    if current.target != value.target or value.observed_target != value.target:
        return ProjectWriteDecision('target_changed')
    if value.proposal_status != 'approved':
        return ProjectWriteDecision('proposal_not_approved')
    # not_applied也已消耗执行机会；未知/已占用/已完成均不能凭新许可重试。
    if value.application_status != 'idle':
        return ProjectWriteDecision('application_not_idle')
    if not value.diff_complete:
        return ProjectWriteDecision('diff_incomplete')
    if value.current_sha256 != value.target.baseline_sha256:
        return ProjectWriteDecision('baseline_changed')
    if value.candidate_sha256 != value.target.proposed_sha256:
        return ProjectWriteDecision('candidate_changed')
    if not value.filesystem_checked:
        return ProjectWriteDecision('filesystem_unconfirmed')
    if not value.platform_supported:
        return ProjectWriteDecision('platform_unsupported')
    return ProjectWriteDecision('eligible')
