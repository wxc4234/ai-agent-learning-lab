from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import shutil
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
processes = []
logs = ExitStack()
try:
    module.engine.__wrapped__(engine)
    from sqlalchemy.orm import Session
    from app.schemas import RegisterRequest
    from app.services.registration_service import register_user

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
    from app.repositories.user_repository import get_user_by_username
    from app.repositories.login_session_repository import create_login_session

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
            'import Link from "next/link"; import HomePage from "./home-page"; export default function Page() { return <><Link href="/login">登录测试页</Link><HomePage /></>; }'
        )
        env = os.environ.copy()
        env.update(
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
                    with urllib.request.urlopen(address, timeout=3) as response:
                        assert response.status == 200
                    break
                except (OSError, TimeoutError):
                    time.sleep(0.5)
            else:
                raise RuntimeError("Temporary service readiness timeout")
        print("Isolated services ready; starting browser verification.", flush=True)
        try:
            subprocess.run(
                [NODE, str(ROOT / "apps/web/test/browser/login-page.mjs")],
                check=True,
                timeout=720,
                env=os.environ | {"AUTH_TEST_BASE_URL": "http://localhost:13000"},
            )
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
