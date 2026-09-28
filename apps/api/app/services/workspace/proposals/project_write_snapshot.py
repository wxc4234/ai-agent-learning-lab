"""可信宿主的只读目标快照；不签发许可、不占用提案、不调用文件替换。"""

import os
import re
from uuid import uuid4

from app.database import SessionLocal
from app.repositories.workspace.file_edit_proposal_repository import read_owned_proposal_application_source
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.files.project_file_observation import observe_project_file
from app.services.workspace.files.workspace_file_replace import _validate_content
from app.services.workspace.proposals.project_write_policy import FileObjectIdentity, ProjectWriteTarget


class ProjectWriteSnapshotError(ValueError):
    def __init__(self) -> None:
        # 数据库、OS或元数据异常只映射为固定未知，不透传路径/正文。
        super().__init__('project_write_snapshot_unavailable')


class ProjectWriteSnapshotReader:
    """可信宿主应长生命周期持有；每次构造生成新实例身份，fork后拒绝使用。

    构造器不接受runtime_id、目录、摘要或许可参数。此实例尚未装配进HTTP/工具。
    快照成功仅描述一次观察，不设置filesystem_checked或exclusive_access_confirmed。
    """

    def __init__(self) -> None:
        self._pid = os.getpid()
        self._runtime_id = uuid4().hex

    def read(self, *, user_id: int, workspace_id: str, task_id: str, proposal_id: str) -> ProjectWriteTarget:
        if os.getpid() != self._pid or type(user_id) is not int or user_id <= 0:
            raise ProjectWriteSnapshotError()
        try:
            # 只读短事务；完整内部主键、候选及修订来自同一次联表授权查询。
            with SessionLocal() as session:
                source = dict(read_owned_proposal_application_source(
                    session, user_id=user_id, workspace_id=workspace_id,
                    task_id=task_id, proposal_id=proposal_id,
                ))
            if (source['status'] != 'approved' or source['application_status'] != 'idle'
                    or source['diff_truncated'] or not source['current_root']
                    or source['current_root'] != source['bound_root']
                    or re.fullmatch(r'[0-9a-f]{64}', source['baseline_sha256']) is None):
                raise ProjectWriteSnapshotError()
            _validate_content(source['proposed_content'], source['proposed_sha256'])
            # 数据库Session已关闭。描述符保留到末次查询结束，文件I/O不持事务。
            with observe_project_file(
                bound_root=source['bound_root'], relative_path=source['relative_path'],
                baseline_sha256=source['baseline_sha256'],
            ) as observed:
                with SessionLocal() as session:
                    latest = dict(read_owned_proposal_application_source(
                        session, user_id=user_id, workspace_id=workspace_id,
                        task_id=task_id, proposal_id=proposal_id,
                    ))
                if latest != source:
                    raise ProjectWriteSnapshotError()
                target = ProjectWriteTarget(
                    runtime_id=self._runtime_id, user_id=user_id,
                    workspace_id=source['workspace_pk'], task_id=source['task_pk'],
                    conversation_id=source['conversation_pk'], proposal_id=source['proposal_pk'],
                    binding_revision=source['binding_revision'], relative_path=source['relative_path'],
                    baseline_sha256=source['baseline_sha256'], proposed_sha256=source['proposed_sha256'],
                    root_identity=FileObjectIdentity(device=observed.root_identity[0], inode=observed.root_identity[1]),
                    file_identity=FileObjectIdentity(device=observed.file_identity[0], inode=observed.file_identity[1]),
                )
            # 观察器退出时仍可能拒绝，不在末次核对/关闭描述符之前返回成功。
            return target
        except WorkspaceNotAccessibleError:
            raise
        except (OSError, ValueError):
            raise ProjectWriteSnapshotError() from None
        except Exception:  # noqa: BLE001 -- DB/native未知失败不产生部分成功结果；中断继续传播。
            raise ProjectWriteSnapshotError() from None
