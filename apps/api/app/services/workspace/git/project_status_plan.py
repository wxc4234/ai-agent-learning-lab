"""普通项目Git状态的纯计划；不授权、不读文件、不启动Git或创建挂载。

Git行为依据：https://git-scm.com/docs/git-status 与 https://git-scm.com/docs/git-config
固定参数只能缩小行为，不能隔离仓库配置或证明源目录可信。后续执行器必须
重新解析Task归属和绑定修订，并准备经检查的独立快照；不能直接运行此计划。
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services.runtime.command.command_environment import build_posix_command_environment
from app.services.workspace.git.status_capture import MAX_STDERR_BYTES, STATUS_TIMEOUT_SECONDS
from app.services.workspace.git.status_parser import MAX_STATUS_BYTES, MAX_STATUS_ENTRIES, MAX_STATUS_PATH_BYTES


class ProjectGitStatusPlanError(ValueError):
    """输入失败只返回固定代码，不回显身份、路径或注入的环境值。"""

    def __init__(self) -> None:
        self.code = 'project_git_status_plan_invalid'
        super().__init__('普通项目Git状态计划参数无效')


class ProjectGitStatusRequest(BaseModel):
    """内部资源引用，绝不是归属凭证；模型工具尚未注册这个协议。"""

    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, revalidate_instances='always')

    plan_id: Literal['project_git_status_v1']
    user_id: int = Field(gt=0, le=2**63 - 1)
    workspace_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    task_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    # 执行前必须从数据库重新读取并比较；请求中的修订不能证明绑定仍有效。
    binding_revision: int = Field(gt=0, le=2**63 - 1)


@dataclass(frozen=True, slots=True)
class ProjectGitStatusPlan:
    """不可变的策略说明，不提供ready/授权成功或直接执行方法。"""

    request: ProjectGitStatusRequest
    argv: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    working_directory: str
    timeout_seconds: float
    max_stdout_bytes: int
    max_stderr_bytes: int
    max_entries: int
    max_path_bytes: int
    required_controls: tuple[str, ...]
    result_scope: Literal['isolated_snapshot_without_submodules']


# 这些是必须由后续实现提供证据的要求，不能由调用者传True将其视为已满足。
_REQUIRED_CONTROLS = (
    'reauthorize_task_workspace_and_binding_revision',
    'close_database_transaction_before_filesystem_or_process_wait',
    'verify_bound_root_identity_and_snapshot_provenance',
    'validate_self_contained_repository_metadata_and_config',
    'reject_unreviewed_includes_filters_helpers_extensions_and_external_metadata',
    'independent_read_only_snapshot_no_host_project_mount',
    'isolated_posix_runtime_with_verified_git_no_network',
    'private_writable_tmp_and_no_inherited_host_environment',
    'bounded_stdout_stderr_complete_eof_and_zero_exit',
    'stop_and_reap_process_on_timeout_cancellation_or_failure',
    'unknown_on_incomplete_evidence_no_automatic_retry',
)


def build_project_git_status_plan(request: object) -> ProjectGitStatusPlan:
    """只构造计划，身份和目录真实性由后续可信适配器核对。

    本函数没有数据库事务。后续适配器应在授权事务结束后准备文件快照，
    在使用前复核绑定，不能让Session跨越文件复制或进程等待。
    """

    try:
        reference = ProjectGitStatusRequest.model_validate(request)
    except ValidationError:
        raise ProjectGitStatusPlanError() from None

    # 全部是将来隔离环境内的固定路径；没有宿主路径、用户命令或env覆盖入口。
    environment = build_posix_command_environment(home_directory='/tmp/home', temporary_directory='/tmp')
    environment.update(
        GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null', GIT_CONFIG_NOSYSTEM='1',
        GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', GIT_NO_REPLACE_OBJECTS='1',
        GIT_NO_LAZY_FETCH='1', GIT_ALLOW_PROTOCOL='',
    )
    # 禁用可选索引刷新/后台监视器，固定机器协议；仍须检查仓库本地config。
    # 不分析子模块内部，不推断重命名，避免把受限范围误报为整个项目干净。
    argv = (
        '/usr/bin/git', '--no-optional-locks', '--no-pager',
        '--git-dir=/workspace/.git', '--work-tree=/workspace',
        '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false',
        '-c', 'core.hooksPath=/dev/null', '-c', 'core.excludesFile=/dev/null',
        '-c', 'core.attributesFile=/dev/null', '-c', 'core.quotePath=false',
        '-c', 'gc.auto=0', '-c', 'maintenance.auto=false', '-c', 'protocol.allow=never',
        'status', '--porcelain=v1', '-z', '--untracked-files=all',
        '--ignore-submodules=all', '--no-renames',
    )
    return ProjectGitStatusPlan(
        request=reference, argv=argv, environment=tuple(sorted(environment.items())),
        working_directory='/workspace', timeout_seconds=STATUS_TIMEOUT_SECONDS,
        max_stdout_bytes=MAX_STATUS_BYTES, max_stderr_bytes=MAX_STDERR_BYTES,
        max_entries=MAX_STATUS_ENTRIES, max_path_bytes=MAX_STATUS_PATH_BYTES,
        required_controls=_REQUIRED_CONTROLS, result_scope='isolated_snapshot_without_submodules',
    )
