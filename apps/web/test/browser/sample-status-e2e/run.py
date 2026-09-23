"""真实只读状态链路：隔离PostgreSQL、API、Next及浏览器，结束后清理自有样例。"""

from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[5]
API = ROOT / 'apps/api'
WEB = ROOT / 'apps/web'
HERE = Path(__file__).resolve().parent
SAMPLE_APP = HERE.parent / 'execution-e2e'
sys.path.insert(0, str(API))
spec = importlib.util.spec_from_file_location('isolated_fixtures', API / 'tests/conftest.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def ready(address, token, processes):
    for _ in range(120):
        if any(process.poll() is not None for process in processes):
            raise RuntimeError('Isolated service exited; inspect sample-status-e2e logs')
        try:
            request = urllib.request.Request(address, headers={'X-Local-Runtime-Token': token})
            with urllib.request.urlopen(request, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, TimeoutError):
            time.sleep(0.5)
    raise RuntimeError('Readiness timeout')


output = WEB / 'output/playwright/sample-status-e2e'
output.mkdir(parents=True, exist_ok=True)
database = fixtures.test_database_url.__wrapped__()
url = next(database)
schema = fixtures.empty_engine.__wrapped__(url)
processes = []
report = None

try:
    engine = next(schema)
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import select, text
    from sqlalchemy.orm import Session
    from app.models import Conversation, FileEditProposal, Task, Workspace, WorkspaceSampleOrigin

    config = Config(str(API / 'alembic.ini'))
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
    with engine.connect() as connection:
        schema_name = connection.scalar(text('SELECT current_schema()'))

    with tempfile.TemporaryDirectory(prefix='agent-status-read-') as directory, ExitStack() as logs:
        temporary = Path(directory)
        web = temporary / 'web'
        (web / 'src/app').mkdir(parents=True)
        (web / 'node_modules').symlink_to(WEB / 'node_modules', target_is_directory=True)
        for file in ('package.json', 'tsconfig.json', 'postcss.config.mjs'):
            shutil.copyfile(WEB / file, web / file)
        for folder in ('features', 'components'):
            shutil.copytree(WEB / 'src' / folder, web / 'src' / folder)
        shutil.copytree(WEB / 'src/app/api/_shared', web / 'src/app/api/_shared')
        routes = (
            'src/app/api/workspaces/[workspaceId]/tasks/[taskId]/sample-status/route.ts',
            'src/app/api/workspaces/[workspaceId]/tasks/[taskId]/file-edit-proposals/[proposalId]/route.ts',
            'src/app/api/workspaces/[workspaceId]/tasks/[taskId]/file-edit-proposals/[proposalId]/application-status/route.ts',
        )
        for route in routes:
            target = web / route
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(WEB / route, target)
        shutil.copyfile(WEB / 'src/app/globals.css', web / 'src/app/globals.css')
        (web / 'next.config.mjs').write_text('export default {reactStrictMode:true};')
        (web / 'src/app/layout.tsx').write_text(
            'import "./globals.css"; export default function Layout({children}: {children: React.ReactNode}) '
            '{return <html lang="zh-CN"><body>{children}</body></html>;}'
        )

        api_port, web_port = port(), port()
        base = f'http://127.0.0.1:{web_port}'
        token = secrets.token_hex(32)
        fixture_file, cleanup_file = temporary / 'fixture.json', temporary / 'cleanup.txt'
        lost_registration = os.environ.get('STATUS_LOST_REGISTRATION') == '1'
        cleanup_pending = os.environ.get('STATUS_CLEANUP_PENDING') == '1'
        assert not (lost_registration and cleanup_pending)
        expected_status = 'sealed' if lost_registration or cleanup_pending else 'ready'
        expected_reason = 'cleanup_pending' if cleanup_pending else 'unavailable' if lost_registration else 'null'
        env = os.environ | {
            'APP_MODE': 'local', 'LOCAL_RUNTIME_TOKEN': token,
            'DATABASE_URL': url.render_as_string(hide_password=False), 'PGOPTIONS': f'-csearch_path={schema_name}',
            'LOGIN_ALLOWED_ORIGINS': json.dumps([base]), 'AUTH_ALLOWED_ORIGINS': base,
            'API_BASE_URL': f'http://127.0.0.1:{api_port}', 'NEXT_TELEMETRY_DISABLED': '1',
            'PYTHONPATH': os.pathsep.join((str(API), str(SAMPLE_APP))),
            'EXECUTION_FIXTURE': str(fixture_file), 'EXECUTION_CLEANUP': str(cleanup_file),
            'STATUS_BASE': base, 'STATUS_OUTPUT': str(output),
            'STATUS_LOST_REGISTRATION': '1' if lost_registration else '0',
            'STATUS_CLEANUP_PENDING': '1' if cleanup_pending else '0',
            'STATUS_EXPECTED': expected_status, 'STATUS_SEALED_REASON': expected_reason,
        }

        def start(args, cwd, name):
            handle = logs.enter_context((output / f'{name}.log').open('w'))
            process = subprocess.Popen(args, cwd=cwd, env=env, stdout=handle, stderr=handle)
            processes.append(process)
            return process

        fixture = None
        try:
            start([str(ROOT / '.venv/bin/python'), '-m', 'uvicorn', 'sample_app:app',
                   '--host', '127.0.0.1', '--port', str(api_port)], API, 'api')
            ready(f'http://127.0.0.1:{api_port}/openapi.json', token, processes)
            fixture = json.loads(fixture_file.read_text())
            sample = Path(fixture['root']) / 'example.txt'
            before_bytes = sample.read_bytes()
            before_stat = sample.stat()
            assert before_bytes == b'old\n'

            # 真正无来源的普通Workspace才应返回missing；同Workspace的其他Task只能得到一般封锁。
            missing_workspace_id = uuid4().hex
            missing_task_id = uuid4().hex
            sibling_task_id = uuid4().hex
            ordinary = temporary / 'ordinary'
            ordinary.mkdir()
            with Session(engine) as session, session.begin():
                source_workspace = session.scalar(select(Workspace).where(Workspace.external_id == fixture['workspace_id']))
                sibling = Task(external_id=sibling_task_id, title='同项目其他任务', workspace=source_workspace)
                sibling_conversation = Conversation(external_id=uuid4().hex, user_id=fixture['user_id'], task=sibling)
                workspace = Workspace(
                    external_id=missing_workspace_id,
                    name='普通项目',
                    root_path=str(ordinary),
                    user_id=fixture['user_id'],
                )
                task = Task(external_id=missing_task_id, title='未登记任务', workspace=workspace)
                conversation = Conversation(external_id=uuid4().hex, user_id=fixture['user_id'], task=task)
                session.add_all([sibling, sibling_conversation, workspace, task, conversation])
            env['STATUS_MISSING_WORKSPACE_ID'] = missing_workspace_id
            env['STATUS_MISSING_TASK_ID'] = missing_task_id
            env['STATUS_SIBLING_TASK_ID'] = sibling_task_id

            with Session(engine) as session:
                proposal = session.scalar(select(FileEditProposal).where(FileEditProposal.external_id == fixture['proposal_id']))
                source_workspace = session.scalar(select(Workspace).where(Workspace.external_id == fixture['workspace_id']))
                origin = session.get(WorkspaceSampleOrigin, source_workspace.id)
                source_task_id = session.scalar(select(Task.id).where(Task.external_id == fixture['task_id']))
                assert origin is not None and origin.task_id == source_task_id
                assert origin.root_path == fixture['root']
                assert origin.lifecycle_state == ('cleanup_pending' if cleanup_pending else 'active')
                assert source_workspace.root_path == (None if cleanup_pending else fixture['root'])
                before_database = (
                    proposal.status, proposal.application_status, source_workspace.root_path,
                    origin.task_id, origin.root_path, origin.lifecycle_state,
                )

            public_scope = {
                'workspaceId': fixture['workspace_id'],
                'taskId': fixture['task_id'],
                'proposalId': fixture['proposal_id'],
            }
            # 页面仅收到公开标识；样例目录、用户编号和内部令牌不进入页面源码。
            (web / 'src/app/page.tsx').write_text(
                'import Detail from "../features/chat/components/file-edit-proposal-detail"; '
                'export default function Page(){return <main className="mx-auto max-w-3xl p-6">'
                '<h1>样例登记真实只读验收</h1><Detail {...' + json.dumps(public_scope) + '} />'
                '</main>;}'
            )
            task_page = web / 'src/app/task-status'
            task_page.mkdir()
            shutil.copyfile(HERE / 'task-fixture.tsx', task_page / 'task-fixture.tsx')
            task_scope = {
                'workspaceId': fixture['workspace_id'],
                'readyTaskId': fixture['task_id'],
                'siblingTaskId': sibling_task_id,
                'missingWorkspaceId': missing_workspace_id,
                'missingTaskId': missing_task_id,
            }
            (task_page / 'page.tsx').write_text(
                'import Fixture from "./task-fixture"; '
                'export default function Page(){return <Fixture {...' + json.dumps(task_scope) + '} />;}'
            )
            start([shutil.which('node'), str(WEB / 'node_modules/next/dist/bin/next'),
                   'dev', '--webpack', '--hostname', '127.0.0.1', '--port', str(web_port)], web, 'web')
            ready(base, token, processes)
            subprocess.run([shutil.which('node'), str(HERE / 'verify.mjs')],
                           cwd=WEB, env=env, check=True, timeout=120)

            # GET链路不能改变样例、Workspace绑定、持久待办或提案数据库状态。
            assert sample.read_bytes() == before_bytes
            after_stat = sample.stat()
            assert after_stat.st_ino == before_stat.st_ino
            assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
            with Session(engine) as session:
                proposal = session.scalar(select(FileEditProposal).where(FileEditProposal.external_id == fixture['proposal_id']))
                workspace = session.scalar(select(Workspace).where(Workspace.external_id == fixture['workspace_id']))
                origin = session.get(WorkspaceSampleOrigin, workspace.id)
                assert proposal.status == 'approved' and proposal.application_status == 'idle'
                assert origin is not None
                assert (
                    proposal.status, proposal.application_status, workspace.root_path,
                    origin.task_id, origin.root_path, origin.lifecycle_state,
                ) == before_database
                ordinary_workspace = session.scalar(select(Workspace).where(Workspace.external_id == missing_workspace_id))
                assert ordinary_workspace.root_path == str(ordinary)
                assert session.get(WorkspaceSampleOrigin, ordinary_workspace.id) is None
            report = json.loads((output / 'browser.json').read_text())
        finally:
            for process in reversed(processes):
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

        assert fixture is not None and report is not None
        assert cleanup_file.read_text() == ('pending-test-cleanup' if cleanup_pending else 'normal')
        assert not Path(fixture['root']).exists()
        with Session(engine) as session:
            workspace = session.scalar(select(Workspace).where(Workspace.external_id == fixture['workspace_id']))
            assert workspace.root_path is None
            assert session.get(WorkspaceSampleOrigin, workspace.id) is None
        api_log = (output / 'api.log').read_text()
        assert f'GET /workspaces/{fixture["workspace_id"]}/tasks/{fixture["task_id"]}/sample-status' in api_log
        assert f'GET /workspaces/{fixture["workspace_id"]}/tasks/{sibling_task_id}/sample-status' in api_log
        report.update(sampleFileUnchanged=True, proposalStillIdle=True, sampleRemoved=True,
                      workspaceUnbound=True, originRemoved=True, servicesStopped=True,
                      originWasCleanupPending=cleanup_pending)
finally:
    # 夹具只删除本轮随机数据库/schema，不接触开发业务表。
    schema.close()
    database.close()

if report is not None:
    report['isolatedDatabaseCleaned'] = True
    (output / 'evidence.json').write_text(json.dumps(report, indent=4))

print('Real sample-status read-only E2E passed; sample, services, schema and database cleaned')
