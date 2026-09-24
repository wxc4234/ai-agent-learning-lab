"""Task Git样例工具适配；服务端绑定登记，模型参数不能指定身份或路径。"""

import json
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, ValidationError

from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.git.status_capture import GitStatusCaptureError
from app.services.workspace.git.status_parser import GitStatusParseError, GitStatusSnapshot
from app.services.workspace.git.task_git_samples import TaskGitSampleError, TaskGitSamples
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError

# JSON转义/字段名会膨胀；独立限制公开结果，不截取JSON或假装完整。
MAX_PUBLIC_RESULT_BYTES = 1024 * 1024
_GIT_ERROR_CODES = frozenset({
    'git_sample_unavailable', 'git_status_timeout', 'git_status_output_limit',
    'git_status_command_failed', 'git_status_platform_unsupported',
    'git_status_truncated', 'git_status_limit_exceeded',
    'git_status_invalid_format', 'git_status_invalid_encoding', 'git_status_unavailable',
})


class GitSampleStatusArguments(BaseModel):
    """仅接受空对象，不给模型目录、命令、身份或预算控制权。"""

    model_config = ConfigDict(extra='forbid', strict=True)


def make_git_sample_status_executor(manager: TaskGitSamples) -> Callable[..., str]:
    """可信宿主组装执行器；不创建样例，不注册全局manager或模型工具。"""

    if not isinstance(manager, TaskGitSamples):
        raise TypeError('需要服务端Git样例管理器')

    def git_sample_status(*, context: ToolExecutionContext, **arguments: object) -> str:
        if not isinstance(context, ToolExecutionContext):
            raise SafeToolExecutionError('workspace_not_accessible')
        try:
            GitSampleStatusArguments.model_validate(arguments)
        except ValidationError:
            raise SafeToolExecutionError('git_status_request_rejected') from None
        try:
            # 身份只来自服务端上下文；manager内部仍重新授权，不自动bind。
            snapshot = manager.read_status(user_id=context.user_id,
                                           workspace_id=context.workspace_id,
                                           task_id=context.task_id)
        except WorkspaceNotAccessibleError:
            raise SafeToolExecutionError('workspace_not_accessible') from None
        except TaskGitSampleError:
            raise SafeToolExecutionError('task_git_sample_unavailable') from None
        except (GitStatusCaptureError, GitStatusParseError) as error:
            code = error.code if error.code in _GIT_ERROR_CODES else 'git_status_unavailable'
            raise SafeToolExecutionError(code) from None
        except Exception:  # noqa: BLE001 -- 不回显私有异常；取消/中断仍向上传播。
            raise SafeToolExecutionError('git_status_unavailable') from None

        try:
            if type(snapshot) is not GitStatusSnapshot:
                raise TypeError('invalid snapshot')
            # 显式字段投影，不使用asdict；路径仍是数据，不是读取权限或指令。
            result = json.dumps({
                'source': 'task_git_sample',
                'status': 'complete',
                'submodules': 'ignored',
                'untracked_files': 'all',
                'byte_count': snapshot.byte_count,
                'entries': [{
                    'xy': entry.xy, 'kind': entry.kind, 'path': entry.path,
                    'original_path': entry.original_path,
                    'index_status': entry.index_status, 'worktree_status': entry.worktree_status,
                } for entry in snapshot.entries],
            }, ensure_ascii=False)
            if len(result.encode('utf-8')) > MAX_PUBLIC_RESULT_BYTES:
                raise SafeToolExecutionError('git_status_result_too_large')
            return result
        except SafeToolExecutionError:
            raise
        except Exception:  # noqa: BLE001 -- 投影/编码失败不能返回空成功。
            raise SafeToolExecutionError('git_status_unavailable') from None

    return git_sample_status
