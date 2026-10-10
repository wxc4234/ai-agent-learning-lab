"""预算、配置和发送白名单在网络前拒绝；不依赖供应商或开发库。"""

import asyncio
import json
from pathlib import Path
import runpy
import sys

import httpx
import pytest

from app.services.workspace.files.code_provider_evaluation import FIXTURE, ProviderPlan, SendBudget
from app.services.workspace.files.code_vector_evaluation import controlled_config
from app.services.workspace.files.code_retrieval_evaluation import load_dataset


def plan(**kwargs):
    config = controlled_config()
    return ProviderPlan(config, config.model, **kwargs)


def request(budget, inputs=None, **changes):
    dataset, _ = load_dataset(FIXTURE)
    payload = budget.plan.payload(inputs or [dataset.queries[0].query])
    payload.update(changes)
    return httpx.Request("POST", budget.plan.config.base_url + "/embeddings", json=payload)


def test_preflight_fixed_corpus_and_redaction():
    manifest = plan().preflight()
    assert manifest["source_count"] == manifest["query_count"] == 8
    assert manifest["planned_requests"] == 9
    assert manifest["cost"] is None
    assert "controlled-fixture-only" not in json.dumps(manifest)
    assert "controlled-fixture-only" not in repr(plan())


@pytest.mark.parametrize("limits", [
    {"max_requests": 8}, {"max_request_bytes": 1}, {"max_total_bytes": 1},
    {"max_requests": True}, {"max_requests": 33}, {"max_total_bytes": -1},
])
def test_invalid_or_insufficient_budget(limits):
    with pytest.raises(ValueError):
        plan(**limits).preflight()


@pytest.mark.parametrize("change", ["model", "dimensions", "extra", "text", "endpoint", "method"])
def test_reject_before_transport(change):
    budget = SendBudget(plan())
    req = request(budget)
    if change == "model":
        req = request(budget, model="other")
    elif change == "dimensions":
        req = request(budget, dimensions=99)
    elif change == "extra":
        req = request(budget, metadata={"private": "data"})
    elif change == "text":
        req = request(budget, inputs=["unapproved project text"])
    elif change == "endpoint":
        req = httpx.Request("POST", "https://other.invalid/embeddings", content=req.content)
    else:
        req = httpx.Request("GET", req.url, content=req.content)
    calls = []
    async def execute():
        transport = budget.transport(mock=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200)))
        try:
            with pytest.raises(ValueError, match="evaluation_send_rejected"):
                await transport.handle_async_request(req)
        finally:
            await transport.aclose()
    asyncio.run(execute())
    assert not calls and budget.requests == 0


def test_replay_and_failed_request_not_refunded():
    budget = SendBudget(plan())
    req = request(budget)
    async def fail(_):
        raise httpx.ConnectError("fixture")
    async def execute():
        transport = budget.transport(mock=httpx.MockTransport(fail))
        try:
            with pytest.raises(httpx.ConnectError):
                await transport.handle_async_request(req)
            with pytest.raises(ValueError):
                await transport.handle_async_request(req)
        finally:
            await transport.aclose()
    asyncio.run(execute())
    assert budget.requests == 1 and budget.bytes == len(req.content)


def test_runtime_body_budget_enforced():
    budget = SendBudget(plan())
    req = request(budget)
    # JSON含大量合法空白：语义相同，但传输字节不能绕过预算。
    req = httpx.Request("POST", req.url, content=req.content + b" " * 40000)
    with pytest.raises(ValueError):
        budget.check(req)


def test_network_requires_opt_in():
    with pytest.raises(ValueError, match="network_not_enabled"):
        SendBudget(plan()).transport()


@pytest.mark.parametrize("args", [
    ["--mode", "real", "--run", "--response-model", "version"],
    ["--mode", "real"],
    ["--run"],
])
def test_cli_missing_explicit_flags(args, monkeypatch):
    script = Path(__file__).resolve().parents[5] / "scripts/evaluate_code_provider.py"
    monkeypatch.setattr(sys, "argv", [str(script), *args])
    with pytest.raises(SystemExit) as caught:
        runpy.run_path(str(script))["main"]()
    assert caught.value.code == 2


def test_cli_missing_config_safe_message(monkeypatch, capsys):
    from app.services.model import embedding_config
    def missing():
        raise ValueError("secret-must-not-appear")
    monkeypatch.setattr(embedding_config, "load_embedding_config", missing)
    script = Path(__file__).resolve().parents[5] / "scripts/evaluate_code_provider.py"
    monkeypatch.setattr(sys, "argv", [str(script), "--mode", "real", "--response-model", "version"])
    assert runpy.run_path(str(script))["main"]() == 2
    assert "secret-must-not-appear" not in capsys.readouterr().err


def test_config_fingerprint_binds_endpoint():
    original = plan()
    changed = ProviderPlan(original.config.model_copy(update={"base_url": "https://other.invalid/v1"}), original.response_model)
    assert original.preflight()["config_sha256"] != changed.preflight()["config_sha256"]


def test_cli_failure_does_not_overwrite_report(monkeypatch, tmp_path):
    import subprocess
    from types import SimpleNamespace

    script = Path(__file__).resolve().parents[5] / "scripts/evaluate_code_provider.py"
    output = tmp_path / "report.json"
    output.write_text("previous")
    monkeypatch.setattr(sys, "argv", [str(script), "--run", "--output", str(output)])
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=7))
    assert runpy.run_path(str(script))["main"]() == 7
    assert output.read_text() == "previous"
