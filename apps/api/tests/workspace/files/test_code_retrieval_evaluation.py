"""离线指标手算、版本/来源拒绝与可复跑CLI；不申请数据库夹具。"""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Literal

import pytest

from app.services.workspace.files.code_retrieval_evaluation import (
    Citation, Dataset, MAX_JSON_BYTES, MAX_SOURCE_BYTES, Prediction, Run,
    dataset_digest, evaluate, lexical_baseline, load_dataset, read_json,
)

ROOT = Path(__file__).resolve().parents[5]
FIXTURE = ROOT / "apps/api/evaluations/code_retrieval/v1"
SCRIPT = ROOT / "scripts/evaluate_code_retrieval.py"


@pytest.fixture
def small():
    data, _ = load_dataset(FIXTURE)
    sources = [dict(source.model_dump(), id=key) for source, key in zip(data.sources[:3], "ABC", strict=True)]
    return Dataset.model_validate({
        "schema_version": 1, "dataset_id": "hand-calculated", "label_origin": "coach_authored",
        "sources": sources,
        "queries": [
            {"id": "q1", "query": "two relevant sources", "relevant_source_ids": ["A", "B"], "rationale": "both required"},
            {"id": "q2", "query": "one relevant source", "relevant_source_ids": ["C"], "rationale": "required"},
            {"id": "q3", "query": "absent", "relevant_source_ids": [], "rationale": "no answer"},
        ],
    })


def prediction(key, ranked=(), *, status: Literal["ok", "error"]="ok", citations=()):
    return Prediction(query_id=key, status=status, ranked_source_ids=list(ranked), citations=list(citations))


def run_for(dataset, predictions):
    return Run(schema_version=1, dataset_sha256=dataset_digest(dataset), strategy="hand-calculated", citation_origin="answer_reference", predictions=predictions)


def test_hand_calculated_duplicates_errors_negatives_and_citations(small):
    start = {s.id: s.start_line for s in small.sources}
    run = run_for(small, [
        prediction("q1", ["C", "A", "A", "B"], citations=[Citation(source_id=key, line=start[key]) for key in "AC"]),
        prediction("q2", status="error"),
        prediction("q3", ["C"], citations=[Citation(source_id="C", line=start["C"])]),
    ])
    report = evaluate(small, run, k=2)
    assert report["recall_at_k"] == 0.25  # q1:1/2, q2失败:0/1，宏平均。
    assert report["mrr_at_k"] == 0.25  # q1首个相关位于2，q2失败为0。
    assert report["error_count"] == 1
    assert report["no_answer_false_positive_count"] == 1
    assert report["citation_relevance"] == pytest.approx(1 / 3)
    assert report["citation_grounding_at_k"] == 1
    assert report["rows"][0]["duplicate_count"] == 1
    assert report["rows"][0]["top_source_ids"] == ["C", "A"]
    assert report["rows"][2]["recall_at_k"] is None
    assert evaluate(small, run, k=3)["recall_at_k"] == 0.5
    assert evaluate(small, run, k=1)["citation_grounding_at_k"] == pytest.approx(2 / 3)


def test_empty_predictions_are_misses_not_dropped_and_missing_citations_unknown(small):
    report = evaluate(small, run_for(small, [prediction(q.id) for q in small.queries]))
    assert report["recall_at_k"] == report["mrr_at_k"] == 0
    assert report["no_answer_false_positive_count"] == 0
    assert report["citation_relevance"] is None and report["citation_grounding_at_k"] is None


def test_all_negative_dataset_has_undefined_positive_metrics_and_error_not_abstention(small):
    data = small.model_dump()
    data["queries"] = [data["queries"][2]]
    dataset = Dataset.model_validate(data)
    report = evaluate(dataset, run_for(dataset, [prediction("q3", status="error")]))
    assert report["recall_at_k"] is None and report["mrr_at_k"] is None
    assert report["no_answer_error_count"] == 1
    assert report["rows"][0]["no_answer_false_positive"] is None


@pytest.mark.parametrize("k", [0, -1, 65, True, 1.0, "3"])
def test_invalid_k(small, k):
    with pytest.raises(ValueError, match="invalid_k"):
        evaluate(small, run_for(small, [prediction(q.id) for q in small.queries]), k=k)


@pytest.mark.parametrize("mode", ["missing", "extra", "duplicate", "wrong_dataset", "unknown_source", "bad_line", "unknown_citation", "duplicate_citation"])
def test_incompatible_predictions_rejected_as_whole(small, mode):
    run = run_for(small, [prediction(q.id) for q in small.queries]).model_dump()
    if mode == "missing":
        run["predictions"].pop()
    elif mode == "extra":
        run["predictions"].append(prediction("other").model_dump())
    elif mode == "duplicate":
        run["predictions"][2] = deepcopy(run["predictions"][0])
    elif mode == "wrong_dataset":
        run["dataset_sha256"] = "0" * 64
    elif mode == "unknown_source":
        # 即使在K之后也要拒绝，不能把坏结果通过裁剪掩盖掉。
        run["predictions"][0]["ranked_source_ids"] = ["A", "B", "C", "unknown"]
    else:
        citation = {"source_id": "unknown" if mode == "unknown_citation" else "A", "line": small.sources[0].start_line}
        if mode == "bad_line":
            citation["line"] = small.sources[0].end_line + 1
        run["predictions"][0]["citations"] = [citation] * (2 if mode == "duplicate_citation" else 1)
    with pytest.raises(ValueError):
        evaluate(small, Run.model_validate(run), k=1)


@pytest.mark.parametrize("change", [
    {"status": "error", "ranked_source_ids": ["A"]}, {"ranked_source_ids": "A"},
    {"status": "unknown"}, {"unknown": 1}, {"citations": [{"source_id": "A", "line": True}]},
])
def test_strict_prediction_contract(change):
    payload = prediction("q1").model_dump() | change
    with pytest.raises(ValueError):
        Prediction.model_validate(payload)


@pytest.mark.parametrize("mode", ["unknown_gold", "duplicate_gold", "duplicate_query", "duplicate_source", "duplicate_span", "empty_query", "extra", "schema"])
def test_dataset_invalid_labels_or_identity(small, mode):
    data = small.model_dump()
    if mode == "unknown_gold":
        data["queries"][0]["relevant_source_ids"] = ["unknown"]
    elif mode == "duplicate_gold":
        data["queries"][0]["relevant_source_ids"] = ["A", "A"]
    elif mode == "duplicate_query":
        data["queries"].append(deepcopy(data["queries"][0]))
    elif mode == "duplicate_source":
        data["sources"][1]["id"] = "A"
    elif mode == "duplicate_span":
        data["sources"][1] = data["sources"][0] | {"id": "B"}
    elif mode == "empty_query":
        data["queries"][0]["query"] = "  "
    elif mode == "extra":
        data["unknown"] = 1
    else:
        data["schema_version"] = 2
    with pytest.raises(ValueError):
        Dataset.model_validate(data)


@pytest.fixture
def copy_fixture(tmp_path):
    path = tmp_path / "dataset"
    shutil.copytree(FIXTURE, path)
    return path


@pytest.mark.parametrize("mode", ["hash", "line", "symbol", "missing", "large", "symlink", "root_symlink", "syntax"])
def test_source_identity_version_and_static_location(copy_fixture, mode):
    path = copy_fixture / "dataset.json"
    data = json.loads(path.read_text())
    source = data["sources"][0]
    file = copy_fixture / "corpus" / source["relative_path"]
    if mode == "hash":
        file.write_text(file.read_text() + "# changed\n")
    elif mode == "line":
        source["start_line"] += 1
    elif mode == "symbol":
        source["symbol"] = "missing"
    elif mode == "missing":
        file.unlink()
    elif mode == "large":
        file.write_bytes(b"x" * (MAX_SOURCE_BYTES + 1))
    elif mode == "symlink":
        file.unlink()
        file.symlink_to(FIXTURE / "corpus" / source["relative_path"])
    elif mode == "root_symlink":
        shutil.rmtree(copy_fixture / "corpus")
        (copy_fixture / "corpus").symlink_to(FIXTURE / "corpus", target_is_directory=True)
    else:
        from hashlib import sha256
        file.write_bytes(b"def broken(\n")
        source["file_sha256"] = sha256(file.read_bytes()).hexdigest()
    path.write_text(json.dumps(data))
    with pytest.raises((ValueError, OSError, SyntaxError)):
        load_dataset(copy_fixture)


@pytest.mark.parametrize("path", ["../outside.py", "/outside.py", "C:/outside.py", "a\\b.py", "a//b.py", "./a.py", "a.txt"])
def test_reject_noncanonical_source_path(copy_fixture, path):
    manifest = copy_fixture / "dataset.json"
    data = json.loads(manifest.read_text())
    data["sources"][0]["relative_path"] = path
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="invalid_source_path"):
        load_dataset(copy_fixture)


def test_json_duplicate_keys_and_budget(tmp_path):
    path = tmp_path / "input.json"
    path.write_text('{"status":"ok","status":"error"}')
    with pytest.raises(ValueError, match="duplicate_json_key"):
        read_json(path)
    path.write_bytes(b" " * (MAX_JSON_BYTES + 1))
    with pytest.raises(ValueError, match="json_too_large"):
        read_json(path)


def test_literal_baseline_is_deterministic_and_does_not_use_gold():
    dataset, texts = load_dataset(FIXTURE)
    run = lexical_baseline(dataset, texts)
    data = dataset.model_dump()
    for query in data["queries"]:
        query["relevant_source_ids"] = []
        query["rationale"] = "changed labels"
    other = Dataset.model_validate(data)
    assert run.predictions == lexical_baseline(other, dict(reversed(list(texts.items())))).predictions
    assert run.dataset_sha256 != dataset_digest(other)
    assert run.citation_origin == "retrieval_reference"
    report = evaluate(dataset, run, k=3)
    expected = json.loads((FIXTURE / "baseline-report.json").read_text())
    assert report == expected
    rows = {row["query_id"]: row for row in report["rows"]}
    assert rows["chinese"]["recall_at_k"] == 0
    assert rows["absent"]["top_source_ids"] == []
    assert rows["absent-related"]["no_answer_false_positive"] is True
    with pytest.raises(ValueError, match="corpus_mismatch"):
        lexical_baseline(dataset, {})


def test_cli_from_unrelated_cwd_and_prediction_input(tmp_path):
    # 子进程只导入离线模块；从无.env的目录也可运行，不需要数据库/模型配置。
    result = subprocess.run([sys.executable, str(SCRIPT)], cwd=tmp_path, capture_output=True, text=True, check=True)
    expected = json.loads((FIXTURE / "baseline-report.json").read_text())
    assert json.loads(result.stdout) == expected
    dataset, texts = load_dataset(FIXTURE)
    predictions = tmp_path / "predictions.json"
    predictions.write_text(lexical_baseline(dataset, texts).model_dump_json())
    output = tmp_path / "report.json"
    subprocess.run([sys.executable, str(SCRIPT), "--predictions", str(predictions), "--output", str(output)], cwd=tmp_path, check=True)
    assert json.loads(output.read_text()) == expected
    invalid = subprocess.run([sys.executable, str(SCRIPT), "--k", "0", "--output", str(tmp_path / "bad.json")], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert invalid.returncode == 2 and "invalid_k" in invalid.stderr
    assert not (tmp_path / "bad.json").exists()


@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_version_is_exact_integer(small, value):
    with pytest.raises(ValueError, match="invalid_schema_version"):
        Dataset.model_validate(small.model_dump() | {"schema_version": value})
    with pytest.raises(ValueError, match="invalid_schema_version"):
        Run.model_validate(run_for(small, [prediction(q.id) for q in small.queries]).model_dump() | {"schema_version": value})


def test_corpus_is_parsed_without_executing_top_level_code(copy_fixture):
    from hashlib import sha256
    manifest = copy_fixture / "dataset.json"
    data = json.loads(manifest.read_text())
    name = data["sources"][0]["relative_path"]
    file = copy_fixture / "corpus" / name
    file.write_text(file.read_text() + "\nraise AssertionError('must never execute corpus')\n")
    for source in data["sources"]:
        if source["relative_path"] == name:
            source["file_sha256"] = sha256(file.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(data))
    _, texts = load_dataset(copy_fixture)
    assert len(texts) == 8


def test_json_exact_size_limit(tmp_path):
    path = tmp_path / "input.json"
    path.write_bytes(b"{}" + b" " * (MAX_JSON_BYTES - 2))
    assert read_json(path) == {}
