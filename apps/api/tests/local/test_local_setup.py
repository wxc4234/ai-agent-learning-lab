"""本地配置生成不能覆盖用户模型配置、泄露凭证或每次更换身份凭证。"""

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("local_setup", ROOT / "scripts/setup_local.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_setup_preserves_config_and_reuses_token(tmp_path, monkeypatch, capsys):
    (tmp_path / "apps/web").mkdir(parents=True)
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=private-test-value\nDATABASE_URL=existing-db\nAPP_MODE=account\n")
    (tmp_path / "apps/web/.env.local").write_text("CUSTOM=value\nAPP_MODE=account\n")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    module.setup()
    first = (tmp_path / ".env").read_text()
    module.setup()
    assert (tmp_path / ".env").read_text() == first
    assert "DEEPSEEK_API_KEY=private-test-value" in first
    assert "DATABASE_URL=existing-db" in first
    token = re.search(r"LOCAL_RUNTIME_TOKEN=([a-f0-9]{64})", first).group(1)
    web = (tmp_path / "apps/web/.env.local").read_text()
    assert "CUSTOM=value" in web
    assert f"LOCAL_RUNTIME_TOKEN={token}" in web
    assert "DEEPSEEK_API_KEY" not in web
    assert first.count("APP_MODE=") == web.count("APP_MODE=") == 1
    output = capsys.readouterr().out
    assert token not in output and "private-test-value" not in output
