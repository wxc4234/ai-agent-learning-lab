"""可控单调时钟验证耗时，合并用量不丢未知与失败样本。"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.services.workspace.files.code_evaluation_observation import Observation


def generated(count=1, prompt=None, total=None):
    return SimpleNamespace(request_count=count, prompt_tokens=prompt, total_tokens=total)


def test_nested_spans_and_nearest_rank():
    times = iter([0, 1, 3, 5])
    obs = Observation(lambda: next(times))
    with obs.span("query", "a", "end_to_end"), obs.span("query", "a", "embedding"):
        pass
    assert [s["duration_ms"] for s in obs.spans] == [2000, 5000]
    for i in range(1, 21):
        ticks = iter([0, i / 1000])
        obs.clock = ticks.__next__
        with obs.span("query", str(i), "recall"):
            pass
    summary = next(r for r in obs.report()["latency"] if r["stage"] == "recall")
    assert summary["count"] == 20
    assert summary["p50_ms"] == 10 and summary["p95_ms"] == 19
    assert summary["mean_ms"] == 10.5


@pytest.mark.parametrize("error,status", [(ValueError, "error"), (asyncio.CancelledError, "cancelled")])
def test_failure_preserved_and_reraised(error, status):
    times = iter([10, 12])
    obs = Observation(lambda: next(times))
    with pytest.raises(error), obs.span("query", "q", "embedding"):
        raise error()
    assert obs.spans[0]["status"] == status and obs.spans[0]["duration_ms"] == 2000


@pytest.mark.parametrize("end", [-1, float("nan"), float("inf")])
def test_bad_clock_rejected(end):
    times = iter([0, end])
    obs = Observation(lambda: next(times))
    with pytest.raises(ValueError, match="invalid_monotonic"), obs.span("query", "q", "embedding"):
        pass


def test_usage_unknown_poison_totals_and_build_separate():
    obs = Observation()
    obs.entry("build", "b").attempts = 1
    obs.accept_usage("build", "b", generated(prompt=100, total=100))
    obs.entry("query", "known").attempts = 1
    obs.accept_usage("query", "known", generated(prompt=0, total=0))
    obs.entry("query", "missing").attempts = 1
    obs.accept_usage("query", "missing", generated())
    obs.entry("query", "before_send")
    totals = {r["phase"]: r for r in obs.report()["usage_totals"]}
    assert totals["build"]["total_tokens"] == 100
    assert totals["query"]["total_tokens"] is None and totals["query"]["unknown_samples"] == 1
    states = {r["sample"]: r["state"] for r in obs.report()["usage"]}
    assert states == {"b": "reported", "known": "reported", "missing": "unknown", "before_send": "not_requested"}


@pytest.mark.parametrize("count,prompt,total", [(True, 1, 1), (0, None, None), (1, True, 1),
    (1, 2, 1), (1, -1, 1), (1, None, 3)])
def test_bad_usage_rejected(count, prompt, total):
    with pytest.raises(ValueError):
        Observation().accept_usage("query", "q", generated(count, prompt, total))


def test_duplicate_and_incomplete_accounting():
    obs = Observation()
    obs.entry("query", "q").attempts = 2
    obs.accept_usage("query", "q", generated(prompt=1, total=1))
    assert obs.report()["usage"][0]["state"] == "unknown"
    assert obs.report()["usage_totals"][0]["total_tokens"] is None
    with pytest.raises(ValueError):
        obs.accept_usage("query", "q", generated())


def test_empty_no_fake_samples():
    report = Observation().report()
    assert report["latency"] == report["usage"] == report["usage_totals"] == []


def test_transport_counts_failure_and_closes():
    obs = Observation()
    class Inner(httpx.AsyncBaseTransport):
        closed = False
        async def handle_async_request(self, request):
            raise httpx.ConnectError("fixture")
        async def aclose(self):
            self.closed = True
    inner = Inner()
    async def execute():
        transport = obs.transport("query", "q", inner)
        with pytest.raises(httpx.ConnectError):
            await transport.handle_async_request(httpx.Request("POST", "https://test.invalid"))
        await transport.aclose()
    asyncio.run(execute())
    assert inner.closed
    assert obs.report()["usage"][0]["attempts"] == 1
    assert obs.report()["usage"][0]["state"] == "unknown"


def test_cli_failed_run_does_not_publish(tmp_path, monkeypatch):
    from pathlib import Path
    import runpy
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[5] / "scripts/evaluate_code_observation.py"
    output = tmp_path / "existing.json"
    output.write_text("previous report")
    monkeypatch.setattr(sys, "argv", [str(script), "--output", str(output)])
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=7))
    assert runpy.run_path(str(script))["main"]() == 7
    assert output.read_text() == "previous report"
