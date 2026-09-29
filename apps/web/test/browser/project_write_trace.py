"""仅隔离验收使用的逐请求追踪；不记录请求正文或任何凭证。"""

import json
import os
from pathlib import Path


def install_trace(app):
    directory = Path(os.environ['BROWSER_TRACE_DIRECTORY'])
    run_id = directory.name

    class TraceMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            path = scope.get('path', '')
            if scope['type'] != 'http' or not path.endswith(('/decision', '/write-grant', '/revoke')):
                return await self.app(scope, receive, send)
            headers = dict(scope['headers'])
            trace_id = headers.get(b'x-isolated-trace-id', b'').decode('ascii', errors='replace')
            result = {'id': trace_id, 'path': path, 'method': scope['method'], 'run': run_id}
            body = bytearray()

            async def traced_send(message):
                if message['type'] == 'http.response.start':
                    result['status'] = message['status']
                    message = {**message, 'headers': [*message.get('headers', []), (b'x-isolated-run', run_id.encode())]}
                if message['type'] == 'http.response.body' and len(body) < 8192:
                    body.extend(message.get('body', b'')[:8192 - len(body)])
                await send(message)

            try:
                await self.app(scope, receive, traced_send)
            finally:
                try:
                    data = json.loads(body)
                    if isinstance(data, dict):
                        result['code'] = data.get('code')
                except (ValueError, UnicodeDecodeError):
                    result['code'] = None
                if result.get('status') == 404:
                    # 独立只读连接辅助区分路由缺失与资源归属拒绝，不改变返回值。
                    from sqlalchemy import select
                    from app.database import SessionLocal
                    from app.models import FileEditProposal, Task, User, Workspace
                    from app.services.auth.local_identity import LOCAL_USER_ID

                    parts = path.split('/')
                    if len(parts) >= 7:
                        with SessionLocal() as session:
                            owner = session.scalar(select(User).where(User.external_id == LOCAL_USER_ID))
                            workspace = session.scalar(select(Workspace).where(Workspace.external_id == parts[2]))
                            task = session.scalar(select(Task).where(Task.external_id == parts[4]))
                            proposal = session.scalar(select(FileEditProposal).where(FileEditProposal.external_id == parts[6]))
                            result['ownership_snapshot'] = {
                                'workspace_exists': workspace is not None,
                                'workspace_owned': owner is not None and workspace is not None and workspace.user_id == owner.id,
                                'task_linked': task is not None and workspace is not None and task.workspace_id == workspace.id,
                                'proposal_linked': proposal is not None and task is not None and proposal.task_id == task.id,
                            }
                with (directory / 'api.jsonl').open('a') as handle:
                    handle.write(json.dumps(result) + '\n')

    app.add_middleware(TraceMiddleware)


def save_final_snapshot(engine, directory):
    """服务停止后、隔离库删除前保留状态，失败轮也不会丢失提交证据。"""
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from app.models import FileEditProposal, ProjectWriteGrantRecord

    with Session(engine) as session:
        proposals = [{'proposal_id': row.external_id, 'status': row.status,
            'application_status': row.application_status, 'has_application_token': row.application_token is not None}
            for row in session.scalars(select(FileEditProposal))]
        grants = [{'grant_id': row.grant_id, 'revision': row.revision, 'enabled': row.enabled}
            for row in session.scalars(select(ProjectWriteGrantRecord))]
    (Path(directory) / 'final-database.json').write_text(json.dumps({'proposals': proposals, 'grants': grants}, indent=4))
