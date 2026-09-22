from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import shutil
import secrets
import subprocess
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[4]
API = ROOT / "apps/api"
NODE = shutil.which("node")
import sys

sys.path.insert(0, str(API))
spec = importlib.util.spec_from_file_location(
    "lesson_conftest", API / "tests/conftest.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
database = module.test_database_url.__wrapped__()
url = next(database)
schema = module.empty_engine.__wrapped__(url)
engine = next(schema)
test_mode = os.environ.get("BROWSER_APP_MODE", "account")
runtime_token = secrets.token_hex(32)
processes = []
logs = ExitStack()
try:
    # 浏览器启动也走真实迁移链，禁止 create_all 绕过启动版本检查。
    from alembic import command
    from alembic.config import Config
    migration_config = Config(str(API / "alembic.ini"))
    with engine.begin() as connection:
        migration_config.attributes["connection"] = connection
        command.upgrade(migration_config, "head")
    from sqlalchemy.orm import Session
    from app.schemas import RegisterRequest
    from app.services.auth.registration_service import register_user

    for username in ("浏览器Agent", "浏览器用户乙"):
        with Session(engine) as session:
            register_user(
                session,
                RegisterRequest.model_validate(
                    {"username": username, "password": "Isolated-Browser-Test-2026!"}
                ),
            )
    from datetime import datetime, UTC, timedelta
    from hashlib import sha256
    from app.repositories.auth.user_repository import get_user_by_username
    from app.repositories.auth.login_session_repository import create_login_session

    with Session(engine) as session:
        user = get_user_by_username(session, "浏览器agent")
        now = datetime.now(UTC)
        create_login_session(
            session,
            user_id=user.id,
            token_hash=sha256(("e" * 43).encode()).hexdigest(),
            created_at=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
        )
        session.commit()
    from sqlalchemy import text

    with engine.connect() as connection:
        schema_name = connection.scalar(text("SELECT current_schema()"))
    with tempfile.TemporaryDirectory(prefix="agent-bff-browser-") as directory:
        web = Path(directory)
        (web / "app/api/auth/login").mkdir(parents=True)
        shutil.copyfile(
            ROOT / "apps/web/src/app/api/auth/login/route.ts",
            web / "app/api/auth/login/route.ts",
        )
        for route in ("sessions/[sessionId]/execution/recover", "sessions/[sessionId]/execution", "runs/[runId]", "auth/register", "workspaces", "workspaces/[workspaceId]/directory", "workspaces/[workspaceId]/directory/select", "workspaces/[workspaceId]/tasks", "workspaces/[workspaceId]/tasks/[taskId]", "workspaces/[workspaceId]/tasks/[taskId]/messages", "workspaces/[workspaceId]/tasks/[taskId]/runs", "workspaces/[workspaceId]/tasks/[taskId]/file-edit-proposals/[proposalId]", "workspaces/[workspaceId]/tasks/[taskId]/file-edit-proposals/[proposalId]/decision", "workspaces/[workspaceId]/tasks/[taskId]/file-edit-proposals/[proposalId]/application-status", "workspaces/[workspaceId]/tasks/[taskId]/title"):
            destination = web / "app/api" / route
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "apps/web/src/app/api" / route / "route.ts", destination / "route.ts")
        (web / "app/api/auth/me").mkdir(parents=True)
        shutil.copyfile(
            ROOT / "apps/web/src/app/api/auth/me/route.ts",
            web / "app/api/auth/me/route.ts",
        )
        (web / "app/api/auth/logout").mkdir(parents=True)
        shutil.copyfile(
            ROOT / "apps/web/src/app/api/auth/logout/route.ts",
            web / "app/api/auth/logout/route.ts",
        )
        (web / "app/api/chat/stream").mkdir(parents=True)
        shutil.copyfile(
            ROOT / "apps/web/src/app/api/chat/stream/route.ts",
            web / "app/api/chat/stream/route.ts",
        )
        (web / "app/api/runs/[runId]/cancel").mkdir(parents=True)
        shutil.copyfile(
            ROOT / "apps/web/src/app/api/runs/[runId]/cancel/route.ts",
            web / "app/api/runs/[runId]/cancel/route.ts",
        )
        shutil.copytree(ROOT / "apps/web/src/app/api/_shared", web / "app/api/_shared")
        shutil.copytree(ROOT / "apps/web/src/app/workspaces", web / "app/workspaces")
        shutil.copyfile(ROOT / "apps/web/package.json", web / "package.json")
        (web / "node_modules").symlink_to(
            ROOT / "apps/web/node_modules", target_is_directory=True
        )
        for folder in ("components", "lib", "features"):
            shutil.copytree(ROOT / "apps/web/src" / folder, web / "src" / folder)
        shutil.copyfile(ROOT / "apps/web/tsconfig.json", web / "tsconfig.json")
        (web / "app/login").mkdir()
        shutil.copyfile(
            ROOT / "apps/web/src/app/login/page.tsx", web / "app/login/page.tsx"
        )
        shutil.copyfile(ROOT / "apps/web/src/app/globals.css", web / "app/globals.css")
        shutil.copyfile(
            ROOT / "apps/web/postcss.config.mjs", web / "postcss.config.mjs"
        )
        (web / "next.config.mjs").write_text("export default {reactStrictMode:true};")
        (web / "app/layout.js").write_text(
            'import "./globals.css"; export default function Layout({children}) { return <html><body>{children}</body></html>; }'
        )
        shutil.copyfile(ROOT / "apps/web/src/app/page.tsx", web / "app/home-page.tsx")
        (web / "app/page.js").write_text(
            'export { default } from "./home-page";'
            if test_mode == "local" else
            'import Link from "next/link"; import HomePage from "./home-page"; export default function Page() { return <><Link href="/login">登录测试页</Link><HomePage /></>; }'
        )
        # 与真实项目保持 src/app 布局，确保 BFF 的相对数据模块导入一致。
        (web / "app").rename(web / "src/app")
        picked_directory = web / "选择的 项目目录"
        picked_directory.mkdir()
        env = os.environ.copy()
        if os.environ.get("BROWSER_TEST_SCRIPT") == "workspace-directory.mjs":
            env["BROWSER_TEST_DIRECTORY"] = str(picked_directory.resolve())
        env.update(
            APP_MODE=test_mode,
            LOCAL_RUNTIME_TOKEN=runtime_token,
            DATABASE_URL=url.render_as_string(hide_password=False),
            PGOPTIONS=f"-csearch_path={schema_name}",
            LOGIN_ALLOWED_ORIGINS='["http://localhost:13000"]',
            LOGIN_COOKIE_SECURE="false",
            PYTHONPATH=os.pathsep.join((str(API), str(Path(__file__).parent))),
        )
        for command, cwd, extra, logname in [
            (
                [
                    str(ROOT / ".venv/bin/python"),
                    "-m",
                    "uvicorn",
                    "chat_test_app:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "18000",
                    "--log-level",
                    "warning",
                ],
                API,
                {},
                "api",
            ),
            (
                [
                    NODE,
                    str(ROOT / "apps/web/node_modules/next/dist/bin/next"),
                    "dev",
                    "--webpack",
                    "--port",
                    "13000",
                    "--hostname",
                    "localhost",
                ],
                web,
                {
                    "API_BASE_URL": "http://127.0.0.1:18000",
                    "AUTH_ALLOWED_ORIGINS": "http://localhost:13000",
                    "NEXT_TELEMETRY_DISABLED": "1",
                },
                "web",
            ),
        ]:
            handle = logs.enter_context(
                open(  # noqa: SIM115 -- ExitStack owns log handles until final cleanup.
                    Path(tempfile.gettempdir())
                    / ("agent-bff-browser-" + logname + ".log"),
                    "w",
                )
            )
            processes.append(
                subprocess.Popen(
                    command, cwd=cwd, env=env | extra, stdout=handle, stderr=handle
                )
            )
        for address in (
            "http://127.0.0.1:18000/openapi.json",
            "http://localhost:13000",
        ):
            for attempt in range(90):
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError(
                        "Temporary service exited; inspect its local log"
                    )
                try:
                    with urllib.request.urlopen(urllib.request.Request(address, headers={"X-Local-Runtime-Token": runtime_token}), timeout=3) as response:
                        assert response.status == 200
                    break
                except (OSError, TimeoutError):
                    time.sleep(0.5)
            else:
                raise RuntimeError("Temporary service readiness timeout")
        browser_fixture = {}
        if os.environ.get("BROWSER_TEST_SCRIPT") == "week4-recovery.mjs":
            # 仅隔离夹具：模拟已退出执行者留下的持久化状态，不向生产增加测试接口。
            import json
            from app.models import Conversation, ConversationExecutionSlot, AgentRun, Message
            from app.services.runtime.execution.execution_process import host_identity
            from sqlalchemy import select
            def create(path, body):
                request = urllib.request.Request(
                    "http://localhost:13000/api" + path,
                    data=json.dumps(body).encode(),
                    headers={"Origin": "http://localhost:13000", "Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.load(response)
            workspace = create("/workspaces", {"name": "异常恢复验收"})
            task = create(f"/workspaces/{workspace['external_id']}/tasks", {"title": "异常退出的任务"})
            dead = subprocess.Popen([sys.executable, "-c", "pass"])
            dead.wait(timeout=10)
            with Session(engine) as session, session.begin():
                conversation = session.scalar(select(Conversation).where(Conversation.external_id == task['conversation_id']))
                session.add(ConversationExecutionSlot(conversation_id=conversation.id, owner_token='c' * 32, owner_host_id=host_identity(), owner_pid=dead.pid))
                session.add(AgentRun(conversation_id=conversation.id, status='running', owner_host_id=host_identity(), owner_pid=dead.pid))
                session.add(Message(conversation_id=conversation.id, role='user', content='异常退出前已持久化的消息'))
            browser_fixture = {"RECOVERY_TASK": json.dumps(task)}
        print("Isolated services ready; starting browser verification.", flush=True)
        try:
            subprocess.run(
                [NODE, str(ROOT / "apps/web/test/browser" / (os.environ.get("BROWSER_TEST_SCRIPT") or ("local-mode.mjs" if test_mode == "local" else "login-page.mjs")))],
                check=True,
                timeout=720,
                env=os.environ | browser_fixture | {"AUTH_TEST_BASE_URL": "http://localhost:13000", "BROWSER_APP_MODE": test_mode, "BROWSER_TEST_DIRECTORY": env.get("BROWSER_TEST_DIRECTORY", "")},
            )
            if os.environ.get("BROWSER_TEST_SCRIPT") == "proposal-tools.mjs":
                from proposal_model import verify_proposal_rows

                verify_proposal_rows(engine)
            if os.environ.get("BROWSER_TEST_SCRIPT") == "proposal-actions.mjs":
                import json
                from sqlalchemy import select
                from app.models import FileEditProposal

                report = json.loads(Path("/private/tmp/agent-ui-proposal-actions/output/playwright/evidence.json").read_text())
                with Session(engine) as session:
                    rows = list(session.scalars(select(FileEditProposal)))
                    assert len(rows) == len(report) == len(os.environ.get(
                        "BROWSER_ACTION_SCENARIOS", "approve,reject,truncated,unknown,safe-error,switch"
                    ).split(","))
                    assert {row.external_id: row.status for row in rows} == {
                        item["proposal_id"]: item["expected"] for item in report
                    }
                print("PASS PostgreSQL: selected scenarios have exact expected decisions, no duplicates.", flush=True)

        finally:
            for process in reversed(processes):
                process.terminate()
            for process in reversed(processes):
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
finally:
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
    logs.close()
    schema.close()
    database.close()
    print(
        "Temporary services stopped; isolated PostgreSQL schema/database cleaned.",
        flush=True,
    )
