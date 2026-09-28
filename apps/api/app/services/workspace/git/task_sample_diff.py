"""应用样例的固定基线差异；独立Git副本不等于TaskGitSamples的仓库。"""

from dataclasses import dataclass
from hashlib import sha256

from app.services.runtime.command.task_command_source import borrow_task_command_source
from app.services.workspace.git import status_capture as git
from app.services.workspace.git.diff_capture import collect_sample_git_diff
from app.services.workspace.samples.task_sample_binding import TaskSampleBindings
from app.services.workspace.samples.temporary_proposal_sample import SAMPLE_CONTENT, SAMPLE_FILENAME
from app.tools.context import ToolExecutionContext


@dataclass(frozen=True)
class TaskSampleDiff:
    # 摘要标识本次读取的字节，不代表后续读取仍是同一版本或已经验证通过。
    baseline_sha256: str
    content_sha256: str
    data: bytes


def read_task_sample_diff(*, context: ToolExecutionContext, bindings: TaskSampleBindings) -> TaskSampleDiff:
    """只用于可信内部集成：固定old基线、固定文件，不提供路径/revision参数。"""
    with borrow_task_command_source(
        user_id=context.user_id, conversation_id=context.conversation_id,
        expected_context=context, bindings=bindings,
    ) as source:
        content = source.read_sample_bytes()
    # 授权事务及来源借用已结束；Git只访问自有副本，不给应用样例添加.git。
    # 不与数据库/后续验证组成原子快照；调用方必须禁止外部并发写者。
    with git.temporary_git_status_sample() as sample:
        file = sample.root / SAMPLE_FILENAME
        file.write_bytes(SAMPLE_CONTENT)
        # worktree diff对比固定index即可，不需要commit、用户身份或任意revision。
        git._capture(
            (git.GIT_EXECUTABLE, '-c', 'core.hooksPath=/dev/null',
             '-c', 'core.autocrlf=false', 'add', '--', SAMPLE_FILENAME),
            cwd=sample.root, env=git._environment(sample.root.parent),
        )
        file.write_bytes(content)
        diff = collect_sample_git_diff(sample, scope='worktree')
    return TaskSampleDiff(sha256(SAMPLE_CONTENT).hexdigest(), sha256(content).hexdigest(), diff.data)
