"""通用配置经过真实隔离PG，默认全程MockTransport。"""

import asyncio
from pathlib import Path
import json
import os
import shutil

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Workspace
from app.repositories.workspace.workspace_repository import set_locked_workspace_root
from app.services.workspace.directory import workspace_path
from app.services.workspace.files import code_query_search, code_vector_search, code_vector_storage
from app.services.workspace.files.code_provider_evaluation import FIXTURE, ProviderPlan
from app.services.workspace.files.code_provider_runner import run_evaluation
from app.services.workspace.files.code_vector_evaluation import controlled_config, controlled_response, controlled_vector
from app.services.model.embedding_config import EmbeddingConfig, load_embedding_config
from tests.assertions import require_value
from tests.workspace.files.test_code_vector_storage import database, target
from tests.model.test_query_embeddings import closed_clients
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError

__all__ = ["closed_clients", "database", "target"]


@pytest.fixture
def bound(database, target, tmp_path, monkeypatch, request):
    root = tmp_path / "corpus"
    shutil.copytree(FIXTURE / "corpus", root)
    request.addfinalizer(lambda: shutil.rmtree(root))
    with Session(database) as session, session.begin():
        set_locked_workspace_root(require_value(session.scalar(select(Workspace))), str(root.resolve()))
    for module in (workspace_path, code_query_search, code_vector_search):
        monkeypatch.setattr(module, "SessionLocal", code_vector_storage.SessionLocal)
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    yield {key: target[key] for key in ("user_id", "workspace_id", "task_id")}
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}


@pytest.mark.parametrize("mode", ["controlled", "development", "alternate", "wrong_version", "provider_error", "cancelled", "unauthorized", "query_version", "query_dimension"])
def test_provider_runner(bound, database, mode, closed_clients):
    config = controlled_config() if mode == "controlled" else EmbeddingConfig(
        api_key=SecretStr("fake-key"), base_url="https://fixture.invalid/v1",
        model="alias", dimensions=67, request_dimensions=True)
    expected = config.model if mode == "controlled" else "reported-v2"
    calls = []

    def respond(request):
        assert database.pool.checkedout() == 0
        calls.append(request)
        if mode == "controlled":
            return controlled_response(request)
        if len(calls) > 1 and mode == "cancelled":
            raise asyncio.CancelledError()
        if len(calls) > 1 and mode == "provider_error":
            return httpx.Response(503)
        inputs = json.loads(request.content)["input"]
        bad_version = mode == "wrong_version" or mode == "query_version" and len(calls) > 1
        padding = 2 if mode == "query_dimension" and len(calls) > 1 else 3
        return httpx.Response(200, json={"object": "list", "model": "wrong" if bad_version else expected,
            "data": [{"object": "embedding", "index": i, "embedding": controlled_vector(text) + [0.0] * padding} for i, text in enumerate(inputs)],
            "usage": {"prompt_tokens": 10, "total_tokens": 12}})

    plan = ProviderPlan(config, expected, dataset_role="distance-development" if mode == "development" else "baseline")
    scope = dict(bound, user_id=-1) if mode == "unauthorized" else bound
    execute = run_evaluation(plan, scope=scope, transport_factory=lambda budget: budget.transport(mock=httpx.MockTransport(respond)))
    if mode == "unauthorized":
        with pytest.raises(WorkspaceNotAccessibleError):
            asyncio.run(execute)
        assert not calls
    elif mode == "wrong_version":
        with pytest.raises(ValueError, match="evaluation_build_space_mismatch"):
            asyncio.run(execute)
        assert len(calls) == 1
    elif mode == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(execute)
        assert len(calls) == 2
    else:
        report = asyncio.run(execute)
        assert report["requests"] == len(calls) == 9
        assert report["vector"]["error_count"] == (8 if mode in ("provider_error", "query_version", "query_dimension") else 0)
        assert report["manifest"]["dimensions"] == config.dimensions
        assert report["observation"]["usage_totals"][0]["total_tokens"] == (None if mode == "controlled" else 12)
        assert report["rrf"]["error_count"] == report["vector"]["error_count"]
        distances = report["distance_observation"]
        assert distances["error_count"] == report["vector"]["error_count"]
        assert len(distances["rows"]) == 8
        for row, scores in zip(distances["rows"], report["vector"]["rows"], strict=True):
            assert row["query_id"] == scores["query_id"] and row["status"] == scores["status"]
            assert [c["source_id"] for c in row["candidates"][:3]] == scores["top_source_ids"]
            if row["status"] == "error":
                assert row["candidates"] == [] and row["nearest_distance"] is None
            else:
                assert len(row["candidates"]) == 8
                assert sum(c["relevant"] for c in row["candidates"]) == scores["relevant_count"]
                assert all(0 <= c["distance"] <= 2 for c in row["candidates"])


    assert all(client.is_closed for client in closed_clients)


def test_provider_command(bound):
    # 只有专用CLI创建的子进程才执行；普通pytest不能因存在模型配置而联网。
    if os.environ.get("CODE_PROVIDER_COMMAND") != "run":
        pytest.skip("explicit command only")
    mode = os.environ["CODE_PROVIDER_MODE"]
    if mode == "real":
        config = load_embedding_config()
        expected = os.environ["CODE_PROVIDER_RESPONSE_MODEL"]
    else:
        config = controlled_config()
        expected = config.model
    limits = json.loads(os.environ["CODE_PROVIDER_LIMITS"])
    dataset_role = os.environ.get("CODE_PROVIDER_DATASET", "baseline")
    assert dataset_role in ("baseline", "distance-development", "distance-holdout")
    plan = ProviderPlan(config, expected, dataset_role=dataset_role, **limits)
    assert plan.preflight() == json.loads(os.environ["CODE_PROVIDER_MANIFEST"])
    report = asyncio.run(run_evaluation(plan, scope=bound,
        transport_factory=lambda budget: budget.transport(allow_network=mode == "real",
            mock=None if mode == "real" else httpx.MockTransport(controlled_response))))
    report["mode"] = mode
    report["observation"]["environment"] = "local PostgreSQL; " + ("configured real provider" if mode == "real" else "controlled HTTPX")
    Path(os.environ["CODE_PROVIDER_REPORT"]).write_text(json.dumps(report, ensure_ascii=False, indent=4) + "\n")


def test_holdout_runner_uses_only_fixed_holdout_text(bound, monkeypatch):
    from hashlib import sha256
    from app.services.workspace.files import code_distance_holdout
    config = controlled_config()
    original = code_distance_holdout.load_frozen_holdout
    def fixture_policy(*args):
        split, policy = original(*args)
        return split, policy.model_copy(update={"config_sha256":sha256(config.model_dump_json().encode()).hexdigest(),
            "requested_model":config.model,"response_model":config.model,"dimensions":config.dimensions})
    monkeypatch.setattr(code_distance_holdout,"load_frozen_holdout",fixture_policy)
    plan = ProviderPlan(config,config.model,dataset_role="distance-holdout")
    dataset,texts=plan.load_dataset()
    calls=[]
    def respond(request):
        calls.extend(json.loads(request.content)["input"])
        return controlled_response(request)
    report=asyncio.run(run_evaluation(plan,scope=bound,transport_factory=lambda budget:budget.transport(mock=httpx.MockTransport(respond))))
    assert calls==[*texts.values(),*(q.query for q in dataset.queries)]
    assert report["requests"]==9 and report["vector"]["error_count"]==0
    assert [row["query_id"] for row in report["distance_observation"]["rows"]]==[q.id for q in dataset.queries]
