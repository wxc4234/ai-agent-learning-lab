"""启动本地 Web/API；数据库与 Redis 由已有 Compose 管理。"""

import os
import shutil
import subprocess
import sys
import time

from dotenv import dotenv_values

from setup_local import ROOT, setup

setup()
# 统一配置来源，两端只绑定回环地址。没有向浏览器暴露内部凭证。
env = os.environ | {key: value for key, value in dotenv_values(ROOT / ".env").items() if value is not None}
pnpm = shutil.which("pnpm")
if pnpm is None:
    raise SystemExit("pnpm is required; see ENVIRONMENT.md")
# 学习期间自动重载 API 源码；环境变量与依赖变化仍需重新运行启动器。
commands = [
    ([sys.executable, "-m", "uvicorn", "app.main:app", "--reload", "--host", "127.0.0.1", "--port", "8000"], ROOT / "apps/api"),
    ([pnpm, "exec", "next", "dev", "--hostname", "127.0.0.1", "--port", "3000"], ROOT / "apps/web"),
]
processes = []
try:
    for command, cwd in commands:
        processes.append(subprocess.Popen(command, cwd=cwd, env=env))
    print("Open http://127.0.0.1:3000 (Ctrl+C stops both services).", flush=True)
    while all(process.poll() is None for process in processes):
        time.sleep(0.5)
except KeyboardInterrupt:
    pass
finally:
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
    for process in reversed(processes):
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
