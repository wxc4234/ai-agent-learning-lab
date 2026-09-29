"""当前项目HEAD/index叶对象完整性校验；仅返回摘要，不返回文件正文。"""
import json
from pydantic import BaseModel, ConfigDict
from app.database import SessionLocal
from app.repositories.workspace.project_git_repository import read_owned_git_binding
from app.services.workspace.git.leaf_objects import read_verified_project_objects
from app.services.workspace.git.project_status_plan import ProjectGitStatusRequest
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError


class VerifyProjectGitArguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


def verify_project_git(*, context: ToolExecutionContext) -> str:
    try:
        # 修订必须从本次授权读取，不能由模型自报可信来源。
        with SessionLocal() as session:
            source = read_owned_git_binding(session, user_id=context.user_id,
                workspace_id=context.workspace_id, task_id=context.task_id)
            revision = source['binding_revision']
        _, result = read_verified_project_objects(ProjectGitStatusRequest(
            plan_id='project_git_status_v1', user_id=context.user_id,
            workspace_id=context.workspace_id, task_id=context.task_id, binding_revision=revision,
        ))
        return json.dumps({'status': 'objects_verified', 'binding_revision': revision,
            'objects': len(result['objects']), 'bytes': sum(map(len, result['objects'].values())),
            'head_entries': len(result['head']), 'index_entries': len(result['index'])})
    except Exception:  # noqa: BLE001 -- 缺失、损坏、格式不支持均不产生部分成功，不泄露正文。
        raise SafeToolExecutionError('project_git_verification_unavailable') from None
