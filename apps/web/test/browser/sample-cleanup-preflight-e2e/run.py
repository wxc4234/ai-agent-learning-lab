"""真实只读诊断链路：隔离 PostgreSQL、API、Next 与 PC 浏览器。"""

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
            raise RuntimeError('Isolated service exited; inspect sample-cleanup-preflight-e2e logs')
        try:
            request = urllib.request.Request(address, headers={'X-Local-Runtime-Token': token})
            with urllib.request.urlopen(request, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, TimeoutError):
            time.sleep(0.5)
    raise RuntimeError('Readiness timeout')


output = WEB / 'output/playwright/sample-cleanup-preflight-e2e'
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

    with tempfile.TemporaryDirectory(prefix='agent-cleanup-read-') as directory, ExitStack() as logs:
        temporary = Path(directory)
        web = temporary / 'web'
        (web / 'src/app').mkdir(parents=True)
        (web / 'node_modules').symlink_to(WEB / 'node_modules', target_is_directory=True)
        for file in ('package.json', 'tsconfig.json', 'postcss.config.mjs'):
            shutil.copyfile(WEB / file, web / file)
        for folder in ('features', 'components'):
            shutil.copytree(WEB / 'src' / folder, web / 'src' / folder)
        shutil.copytree(WEB / 'src/app/api/_shared', web / 'src/app/api/_shared')
        route = 'src/app/api/workspaces/[workspaceId]/tasks/[taskId]/sample-cleanup-preflight/route.ts'
        target = web / route
        target.parent.mkdir(parents=True)
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
        fixture_file = temporary / 'fixture.json'
        cleanup_file = temporary / 'cleanup.txt'
        env = os.environ | {
            'APP_MODE': 'local',
            'LOCAL_RUNTIME_TOKEN': token,
            'DATABASE_URL': url.render_as_string(hide_password=False),
            'PGOPTIONS': f'-csearch_path={schema_name}',
            'LOGIN_ALLOWED_ORIGINS': json.dumps([base]),
            'AUTH_ALLOWED_ORIGINS': base,
            'API_BASE_URL': f'http://127.0.0.1:{api_port}',
            'NEXT_TELEMETRY_DISABLED': '1',
            'PYTHONPATH': os.pathsep.join((str(API), str(SAMPLE_APP))),
            'EXECUTION_FIXTURE': str(fixture_file),
            'EXECUTION_CLEANUP': str(cleanup_file),
            'STATUS_CLEANUP_PENDING': '1',
            'PREFLIGHT_BASE': base,
            'PREFLIGHT_OUTPUT': str(output),
        }

        def start(args, cwd, name):
            handle = logs.enter_context((output / f'{name}.log').open('w'))
            process = subprocess.Popen(args, cwd=cwd, env=env, stdout=handle, stderr=handle)
            processes.append(process)

        fixture = None
        try:
            start(
                [str(ROOT / '.venv/bin/python'), '-m', 'uvicorn', 'sample_app:app',
                 '--host', '127.0.0.1', '--port', str(api_port)],
                API, 'api',
            )
            ready(f'http://127.0.0.1:{api_port}/openapi.json', token, processes)
            fixture = json.loads(fixture_file.read_text())
            sample = Path(fixture['root']) / 'example.txt'
            before_bytes = sample.read_bytes()
            before_file = sample.stat()
            assert before_bytes == b'old\n'

            sibling_task_id = uuid4().hex
            with Session(engine) as session, session.begin():
                workspace = session.scalar(select(Workspace).where(Workspace.external_id == fixture['workspace_id']))
                sibling = Task(external_id=sibling_task_id, title='同项目其他任务', workspace=workspace)
                conversation = Conversation(external_id=uuid4().hex, user_id=fixture['user_id'], task=sibling)
                session.add_all([sibling, conversation])
            env['PREFLIGHT_SIBLING_TASK_ID'] = sibling_task_id

            with Session(engine) as session:
                proposal = session.scalar(select(FileEditProposal).where(FileEditProposal.external_id == fixture['proposal_id']))
                workspace = session.scalar(select(Workspace).where(Workspace.external_id == fixture['workspace_id']))
                origin = session.get(WorkspaceSampleOrigin, workspace.id)
                source_task_id = session.scalar(select(Task.id).where(Task.external_id == fixture['task_id']))
                assert origin is not None and origin.task_id == source_task_id
                assert origin.lifecycle_state == 'cleanup_pending' and workspace.root_path is None
                assert origin.root_path == fixture['root']
                assert (origin.parent_dev, origin.parent_ino) == (
                    Path(fixture['root']).parent.stat().st_dev,
                    Path(fixture['root']).parent.stat().st_ino,
                )
                assert (origin.root_dev, origin.root_ino) == (
                    Path(fixture['root']).stat().st_dev,
                    Path(fixture['root']).stat().st_ino,
                )
                before_database = (
                    proposal.status, proposal.application_status, workspace.root_path,
                    origin.task_id, origin.root_path, origin.lifecycle_state,
                    origin.parent_dev, origin.parent_ino, origin.root_dev, origin.root_ino,
                )

            # 临时页面只获得公开的 Workspace/Task 标识，不接触目录或服务端令牌。
            shutil.copyfile(HERE / 'task-fixture.tsx', web / 'src/app/task-fixture.tsx')
            public_scope = {
                'workspaceId': fixture['workspace_id'],
                'sourceTaskId': fixture['task_id'],
                'siblingTaskId': sibling_task_id,
            }
            (web / 'src/app/page.tsx').write_text(
                'import Fixture from "./task-fixture"; '
                'export default function Page(){return <Fixture {...'
                + json.dumps(public_scope) + '} />;}'
            )
            start(
                [shutil.which('node'), str(WEB / 'node_modules/next/dist/bin/next'),
                 'dev', '--webpack', '--hostname', '127.0.0.1', '--port', str(web_port)],
                web, 'web',
            )
            ready(base, token, processes)
            subprocess.run(
                [shutil.which('node'), str(HERE / 'verify.mjs')],
                cwd=WEB, env=env, check=True, timeout=120,
            )

            # 只读 GET 前后核对文件和数据库，不以 HTTP 200 推断没有副作用。
            assert sample.read_bytes() == before_bytes
            after_file = sample.stat()
            assert after_file.st_ino == before_file.st_ino
            assert after_file.st_mtime_ns == before_file.st_mtime_ns
            with Session(engine) as session:
                proposal = session.scalar(select(FileEditProposal).where(FileEditProposal.external_id == fixture['proposal_id']))
                workspace = session.scalar(select(Workspace).where(Workspace.external_id == fixture['workspace_id']))
                origin = session.get(WorkspaceSampleOrigin, workspace.id)
                assert origin is not None
                assert (
                    proposal.status, proposal.application_status, workspace.root_path,
                    origin.task_id, origin.root_path, origin.lifecycle_state,
                    origin.parent_dev, origin.parent_ino, origin.root_dev, origin.root_ino,
                ) == before_database
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
        assert cleanup_file.read_text() == 'pending-test-cleanup'
        assert not Path(fixture['root']).exists()
        with Session(engine) as session:
            workspace = session.scalar(select(Workspace).where(Workspace.external_id == fixture['workspace_id']))
            assert workspace.root_path is None
            assert session.get(WorkspaceSampleOrigin, workspace.id) is None
        api_log = (output / 'api.log').read_text()
        for task_id in (fixture['task_id'], sibling_task_id, 'f' * 32):
            assert f'GET /workspaces/{fixture["workspace_id"]}/tasks/{task_id}/sample-cleanup-preflight' in api_log
        report.update(
            sampleFileUnchanged=True,
            proposalStillIdle=True,
            originUnchangedBeforeCleanup=True,
            sampleRemoved=True,
            workspaceUnbound=True,
            originRemoved=True,
            servicesStopped=True,
        )
finally:
    # 测试夹具只删除本轮随机数据库/schema，不触碰开发业务表。
    schema.close()
    database.close()

if report is not None:
    report['isolatedDatabaseCleaned'] = True
    (output / 'evidence.json').write_text(json.dumps(report, indent=4))

print('Real sample-cleanup-preflight read-only E2E passed; sample, services and database cleaned')
