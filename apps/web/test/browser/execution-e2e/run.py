"""真实BFF/API/浏览器专项；独立PostgreSQL库、schema、进程与自有样例。"""

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

ROOT = Path(__file__).resolve().parents[5]
API = ROOT / 'apps/api'
WEB = ROOT / 'apps/web'
HERE = Path(__file__).resolve().parent
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
            raise RuntimeError('Isolated service exited; inspect execution-e2e logs')
        try:
            with urllib.request.urlopen(urllib.request.Request(address, headers={'X-Local-Runtime-Token': token}), timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, TimeoutError):
            time.sleep(0.5)
    raise RuntimeError('Readiness timeout')


output = WEB / 'output/playwright/execution-e2e'
output.mkdir(parents=True, exist_ok=True)
database = fixtures.test_database_url.__wrapped__()
url = next(database)
schema = fixtures.empty_engine.__wrapped__(url)
processes = []
try:
    engine = next(schema)
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import select, text
    from sqlalchemy.orm import Session
    from app.models import FileEditProposal, Workspace

    config = Config(str(API / 'alembic.ini'))
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
    with engine.connect() as connection:
        schema_name = connection.scalar(text('SELECT current_schema()'))
    with tempfile.TemporaryDirectory(prefix='agent-real-apply-') as directory, ExitStack() as logs:
        temporary = Path(directory)
        web = temporary / 'web'
        (web / 'src/app').mkdir(parents=True)
        (web / 'node_modules').symlink_to(WEB / 'node_modules', target_is_directory=True)
        for file in ('package.json', 'tsconfig.json', 'postcss.config.mjs'):
            shutil.copyfile(WEB / file, web / file)
        for folder in ('features', 'components'):
            shutil.copytree(WEB / 'src' / folder, web / 'src' / folder)
        shutil.copytree(WEB / 'src/app/api/_shared', web / 'src/app/api/_shared')
        route = Path('src/app/api/workspaces/[workspaceId]/tasks/[taskId]/file-edit-proposals/[proposalId]/apply/route.ts')
        (web / route).parent.mkdir(parents=True)
        shutil.copyfile(WEB / route, web / route)
        shutil.copyfile(WEB / 'src/app/globals.css', web / 'src/app/globals.css')
        (web / 'next.config.mjs').write_text('export default {reactStrictMode:true};')
        (web / 'src/app/layout.tsx').write_text('import "./globals.css"; export default function Layout({children}: {children: React.ReactNode}) {return <html lang="zh-CN"><body>{children}</body></html>;}')
        api_port, web_port = port(), port()
        base = f'http://127.0.0.1:{web_port}'
        token = secrets.token_hex(32)
        fixture_file, cleanup_file = temporary / 'fixture.json', temporary / 'cleanup.txt'
        env = os.environ | {
            'APP_MODE': 'local', 'LOCAL_RUNTIME_TOKEN': token,
            'DATABASE_URL': url.render_as_string(hide_password=False), 'PGOPTIONS': f'-csearch_path={schema_name}',
            'LOGIN_ALLOWED_ORIGINS': json.dumps([base]), 'AUTH_ALLOWED_ORIGINS': base,
            'API_BASE_URL': f'http://127.0.0.1:{api_port}', 'NEXT_TELEMETRY_DISABLED': '1',
            'PYTHONPATH': os.pathsep.join((str(API), str(HERE))),
            'EXECUTION_FIXTURE': str(fixture_file), 'EXECUTION_CLEANUP': str(cleanup_file),
            'EXECUTION_BASE': base, 'EXECUTION_OUTPUT': str(output),
        }
        def start(args, cwd, name):
            handle = logs.enter_context((output / f'{name}.log').open('w'))
            process = subprocess.Popen(args, cwd=cwd, env=env, stdout=handle, stderr=handle)
            processes.append(process)
            return process
        try:
            api = start([str(ROOT / '.venv/bin/python'), '-m', 'uvicorn', 'sample_app:app', '--host', '127.0.0.1', '--port', str(api_port)], API, 'api')
            ready(f'http://127.0.0.1:{api_port}/openapi.json', token, processes)
            fixture = json.loads(fixture_file.read_text())
            sample = Path(fixture['root']) / 'example.txt'
            assert sample.read_bytes() == b'old\n'
            with Session(engine) as session:
                proposal = session.scalar(select(FileEditProposal).where(FileEditProposal.external_id == fixture['proposal_id']))
                assert proposal.status == 'approved' and proposal.application_status == 'idle'
            public_scope = {'workspaceId': fixture['workspace_id'], 'taskId': fixture['task_id'], 'proposalId': fixture['proposal_id']}
            # 仅向页面注入公开资源编号，绝不注入root或内部凭证。
            (web / 'src/app/page.tsx').write_text('import Panel from "../features/chat/components/proposal-execution-actions"; export default function Page(){return <main className="mx-auto max-w-3xl p-6"><h1>真实样例应用验收</h1><Panel {...' + json.dumps(public_scope) + '} /></main>;}')
            start([shutil.which('node'), str(WEB / 'node_modules/next/dist/bin/next'), 'dev', '--webpack', '--hostname', '127.0.0.1', '--port', str(web_port)], web, 'web')
            ready(base, token, processes)
            subprocess.run([shutil.which('node'), str(HERE / 'verify.mjs')], cwd=WEB, env=env, check=True, timeout=120)
            assert sample.read_bytes() == b'new\n'
            with Session(engine) as session:
                proposal = session.scalar(select(FileEditProposal).where(FileEditProposal.external_id == fixture['proposal_id']))
                assert proposal.application_status == 'applied'
            report = json.loads((output / 'browser.json').read_text())
            report.update(beforeFile='old\\n', afterFile='new\\n', beforeDatabase='idle', afterDatabase='applied')
        finally:
            for process in reversed(processes):
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        assert cleanup_file.read_text() == 'sealed-test-cleanup'
        assert not Path(fixture['root']).exists()
        with Session(engine) as session:
            workspace = session.scalar(select(Workspace).where(Workspace.external_id == fixture['workspace_id']))
            assert workspace.root_path is None
        report.update(sampleRemoved=True, workspaceUnbound=True, servicesStopped=True, normalCloseRefused=True, cleanup='isolated fixture only after confirmed applied state and stopped requests')
        (output / 'evidence.json').write_text(json.dumps(report, indent=4))
finally:
    # 固定夹具只清理本轮随机测试schema/database；不操作开发业务表。
    schema.close()
    database.close()
print('Real execution E2E passed; sample, services, schema and database cleaned')
