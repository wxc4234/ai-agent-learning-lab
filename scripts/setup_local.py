"""生成本机服务凭证并同步两端配置；不打印密钥或覆盖其他配置。"""

import re
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def update_env(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    # 删除同名旧配置再写入一份，防止重复键造成解释不一致。
    lines = [line for line in lines if line.split("=", 1)[0].strip() not in values]
    path.write_text("\n".join(lines + [f"{key}={value}" for key, value in values.items()]) + "\n")
    path.chmod(0o600)


def setup() -> None:
    api_env = ROOT / ".env"
    if not api_env.exists():
        api_env.write_text((ROOT / ".env.example").read_text())
    existing = dict(line.split("=", 1) for line in api_env.read_text().splitlines() if "=" in line and not line.startswith("#"))
    token = existing.get("LOCAL_RUNTIME_TOKEN", "")
    if not re.fullmatch(r"[a-f0-9]{64}", token):
        token = secrets.token_hex(32)
    values = {"APP_MODE": "local", "LOCAL_RUNTIME_TOKEN": token}
    update_env(api_env, values)
    update_env(ROOT / "apps/web/.env.local", values | {"API_BASE_URL": "http://127.0.0.1:8000"})
    print("Local configuration ready. Model credentials and existing database settings preserved.")


if __name__ == "__main__":
    setup()
