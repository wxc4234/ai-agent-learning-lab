"""Task暂存观察HTTP入口；同步线程执行，来源和错误均显式投影。"""

import re
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import CurrentUser
from app.database import SessionLocal
from app.repositories.workspace.project_git_repository import read_owned_git_binding
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.routers.workspace.boundary import WorkspaceRoute
from app.routers.workspace.http import _error_response
from app.routers.workspace.parameters import TaskIdentifier
from app.schemas import WorkspaceErrorResponse
from app.services.workspace.git.project_source import ProjectGitSourceError
from app.services.workspace.git.project_staged import read_project_staged

MAX_RESPONSE_BYTES = 1024 * 1024


class PublicModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)


class StagedVersion(PublicModel):
    mode: Literal[0o100644, 0o100755, 0o120000, 0o160000]
    object_id: str = Field(pattern=r'^[0-9a-f]{40}$')


class StagedIndexVersion(PublicModel):
    stage: Literal[0, 1, 2, 3]
    version: StagedVersion


class StagedItem(PublicModel):
    path: str = Field(min_length=1, max_length=4096)
    status: Literal['added', 'deleted', 'modified', 'unmerged']
    head: StagedVersion | None
    index: list[StagedIndexVersion] = Field(max_length=3)


class StagedResponse(PublicModel):
    workspace_id: str
    task_id: str
    binding_revision: int
    scope: Literal['staged_only'] = 'staged_only'
    status: Literal['staged_compared', 'comparison_unavailable']
    unavailable_reasons: list[Literal['no_head_object_observed', 'commit_object_missing', 'index_missing']]
    changes: list[StagedItem] | None = Field(max_length=4000)


router = APIRouter(prefix='/workspaces', tags=['workspaces'], route_class=WorkspaceRoute)

# 只映射已知分类；未知错误码不通过前缀匹配反射给客户端。
_CONFLICT = {'project_git_source_stale', 'project_git_source_changed', 'project_git_layout_changed',
             'project_git_config_changed', 'project_git_head_changed'}
_LIMIT = {'git_pack_limit', 'project_git_layout_limit', 'project_git_config_limit', 'project_git_head_limit',
          'project_git_packed_limit', 'project_git_commit_limit', 'loose_tree_limit', 'index_v2_limit',
          'project_git_tree_graph_limit', 'staged_compare_limit'}
_UNSUPPORTED = {
    'git_pack_invalid', 'git_pack_checksum', 'git_pack_missing_base',
    'project_git_source_unbound', 'project_git_source_platform_unsupported',
    'project_git_layout_missing', 'project_git_layout_unsupported', 'project_git_config_unsupported',
    'project_git_head_missing', 'project_git_head_unsupported', 'project_git_packed_incomplete',
    'project_git_packed_unsupported', 'project_git_commit_compression_invalid', 'project_git_commit_hash_mismatch',
    'project_git_commit_size_mismatch', 'project_git_commit_unsupported',
    'loose_tree_compression_invalid', 'loose_tree_encoding_unsupported', 'loose_tree_hash_mismatch',
    'loose_tree_invalid', 'loose_tree_mode_unsupported', 'loose_tree_name_unsupported',
    'loose_tree_object_id_unsupported', 'loose_tree_order_invalid', 'loose_tree_size_mismatch',
    'index_v2_checksum_mismatch', 'index_v2_encoding_unsupported', 'index_v2_extensions_unsupported',
    'index_v2_flags_unsupported', 'index_v2_invalid', 'index_v2_object_id_unsupported', 'index_v2_order_invalid',
    'index_v2_path_invalid', 'index_v2_stage_conflict', 'index_v2_version_unsupported',
    'project_git_tree_graph_cycle', 'project_git_tree_graph_object_missing',
}


@router.get('/{workspace_id}/tasks/{task_id}/git/staged', response_model=StagedResponse,
            responses={n: {'model': WorkspaceErrorResponse} for n in (403, 404, 409, 422, 500)})
def read_staged(workspace_id: TaskIdentifier, task_id: TaskIdentifier,
                request: Request, current_user: CurrentUser) -> Response:
    # 不接受身份/路径/授权字段，也不接受重复修订；按十进制规范字符串验证。
    pairs = list(request.query_params.multi_items())
    if (len(pairs) != 1 or pairs[0][0] != 'binding_revision'
            or re.fullmatch(r'[1-9][0-9]{0,15}', pairs[0][1]) is None
            or int(pairs[0][1]) > 9007199254740991):
        return _error_response(422, code='invalid_staged_input', message='仅接受有效的当前绑定修订')
    revision = int(pairs[0][1])
    try:
        observed = read_project_staged({'plan_id': 'project_git_status_v1', 'user_id': current_user.id,
                                        'workspace_id': workspace_id, 'task_id': task_id, 'binding_revision': revision})
        config = observed.index.config
        source = config.layout.source
        # 服务回执必须属于本次身份/修订并共用同一观察来源，不能把两个旧结果拼接。
        if (observed.head.commit.head.config is not config or source.user_id != current_user.id
                or source.binding_revision != revision
                or (observed.changes is None) != (observed.status == 'comparison_unavailable')
                or bool(observed.unavailable_reasons) != (observed.changes is None)):
            raise ValueError('invalid_observation')
        def version(value):
            return None if value is None else {'mode': value.mode, 'object_id': value.object_id}
        payload = StagedResponse.model_validate({
            'workspace_id': workspace_id, 'task_id': task_id, 'binding_revision': revision,
            'status': observed.status, 'unavailable_reasons': list(observed.unavailable_reasons),
            'changes': None if observed.changes is None else [
                {'path': item.path, 'status': item.status, 'head': version(item.head),
                 'index': [{'stage': v.stage, 'version': version(v.version)} for v in item.index]}
                for item in observed.changes
            ],
        })
        # 对最终UTF-8 JSON字节计费，超限整次拒绝，不截断为貌似完整的差异。
        body = payload.model_dump_json().encode('utf-8')
        if len(body) > MAX_RESPONSE_BYTES:
            return _error_response(422, code='staged_response_limit', message='暂存差异响应超过当前支持的大小')
        return Response(body, media_type='application/json', headers={'Cache-Control': 'no-store'})
    except WorkspaceNotAccessibleError:
        raise  # 沿用统一404，避免区分不存在与无权访问。
    except ProjectGitSourceError as exc:
        if exc.code in _CONFLICT:
            return _error_response(409, code='staged_observation_changed', message='绑定或项目内容已变化，请重新查询')
        if exc.code in _LIMIT:
            return _error_response(422, code='staged_observation_limit', message='项目超出当前观察预算')
        if exc.code in _UNSUPPORTED:
            return _error_response(422, code='staged_observation_unsupported', message='项目格式不在当前支持范围或数据不完整')
        return _error_response(500, code='staged_read_failed', message='暂存差异读取失败，结果未知')
    except Exception:  # noqa: BLE001 -- 公开边界不回显内部对象或异常
        return _error_response(500, code='staged_read_failed', message='暂存差异读取失败，结果未知')


class StagedBindingResponse(PublicModel):
    workspace_id: str
    task_id: str
    binding_revision: int
    bound: bool


@router.get('/{workspace_id}/tasks/{task_id}/git/binding', response_model=StagedBindingResponse)
def read_staged_binding(workspace_id: TaskIdentifier, task_id: TaskIdentifier,
                        request: Request, current_user: CurrentUser) -> StagedBindingResponse | Response:
    # 仅用于取得当前修订，不打开路径，也不向浏览器返回宿主目录。
    if request.query_params:
        return _error_response(422, code='invalid_staged_input', message='绑定查询不接受额外参数')
    with SessionLocal() as session:
        row = read_owned_git_binding(session, user_id=current_user.id, workspace_id=workspace_id, task_id=task_id)
        return StagedBindingResponse(workspace_id=workspace_id, task_id=task_id,
                                     binding_revision=row['binding_revision'], bound=row['root_path'] is not None)
