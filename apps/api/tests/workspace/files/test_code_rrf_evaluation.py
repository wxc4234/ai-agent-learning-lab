"""手算RRF、稳定排序、失败语义与输入边界；不启动数据库。"""

from fractions import Fraction

import pytest

from app.services.workspace.files.code_retrieval_evaluation import Citation, Dataset, Prediction, Run, dataset_digest, evaluate
from app.services.workspace.files.code_rrf_evaluation import fuse_rrf
from tests.workspace.files.test_code_retrieval_evaluation import small

__all__ = ["small"]


def runs(dataset, left=(), right=(), *, errors=()):
    result = []
    for name, ordered in (("lexical", left), ("vector", right)):
        predictions = [Prediction(query_id=q.id, status="error" if name in errors and q.id == "q1" else "ok",
                                  ranked_source_ids=[] if name in errors or q.id != "q1" else list(ordered), citations=[]) for q in dataset.queries]
        result.append(Run(schema_version=1, dataset_sha256=dataset_digest(dataset), strategy=name, citation_origin="retrieval_reference", predictions=predictions))
    return result


def test_hand_calculated_dedup_missing_and_exact_tie(small):
    left, right = runs(small, ["A", "A", "B"], ["B", "C", "C", "A"])
    result = fuse_rrf(small, left, right, rank_constant=1)
    assert result.run.predictions[0].ranked_source_ids == ["B", "A", "C"]
    rows = {row["source_id"]: row for row in result.diagnostics[0]["candidates"]}
    assert rows["B"]["score_fraction"] == str(Fraction(1, 3) + Fraction(1, 2))
    assert rows["A"]["score_fraction"] == "3/4"
    assert rows["C"]["ranks"] == {"lexical": None, "vector": 2}
    assert rows["C"]["score_fraction"] == "1/3"
    assert result.run.predictions[0].citations[0].source_id == "B"
    # 对称排名分数相等，按来源ID而非哪一路先传入排序。
    tied = fuse_rrf(small, *runs(small, ["B", "A"], ["A", "B"]))
    assert tied.run.predictions[0].ranked_source_ids == ["A", "B"]
    assert tied.diagnostics[0]["candidates"][0]["score_fraction"] == tied.diagnostics[0]["candidates"][1]["score_fraction"]


def test_input_order_and_gold_do_not_change_ranking(small):
    left, right = runs(small, ["C", "B", "A"], ["B", "A"])
    first = fuse_rrf(small, left, right)
    assert first == fuse_rrf(small, right, left)
    data = small.model_dump()
    for query in data["queries"]:
        query["relevant_source_ids"] = []
        query["rationale"] = "different labels"
    changed = Dataset.model_validate(data)
    other = fuse_rrf(changed, *runs(changed, ["C", "B", "A"], ["B", "A"]))
    assert first.run.predictions == other.run.predictions
    assert first.diagnostics == other.diagnostics
    assert first.run.dataset_sha256 != other.run.dataset_sha256


@pytest.mark.parametrize("errors", [("lexical",), ("vector",), ("lexical", "vector")])
def test_input_error_never_silently_falls_back(small, errors):
    result = fuse_rrf(small, *runs(small, ["A"], ["B"], errors=errors))
    prediction = result.run.predictions[0]
    assert prediction.status == "error" and not prediction.ranked_source_ids and not prediction.citations
    assert result.diagnostics[0]["failed_strategies"] == sorted(errors)
    report = evaluate(small, result.run)
    assert report["error_count"] == 1 and report["recall_at_k"] == 0


@pytest.mark.parametrize("left,right,expected", [([], [], []), (["C", "A"], [], ["C", "A"]), ([], ["B"], ["B"])])
def test_successful_empty_is_not_error(small, left, right, expected):
    result = fuse_rrf(small, *runs(small, left, right))
    assert result.run.predictions[0].status == "ok"
    assert result.run.predictions[0].ranked_source_ids == expected
    assert bool(result.run.predictions[0].citations) == bool(expected)


@pytest.mark.parametrize("constant", [0, -1, 10001, True, 1.0, "60", None])
def test_invalid_constant(small, constant):
    with pytest.raises(ValueError, match="invalid_rrf_constant"):
        fuse_rrf(small, *runs(small), rank_constant=constant)


@pytest.mark.parametrize("constant", [1, 60, 10000])
def test_constant_boundaries_and_full_union_before_k(small, constant):
    result = fuse_rrf(small, *runs(small, ["C", "A", "B"], ["B", "A", "C"]), rank_constant=constant)
    assert set(result.run.predictions[0].ranked_source_ids) == {"A", "B", "C"}
    assert len(evaluate(small, result.run, k=1)["rows"][0]["top_source_ids"]) == 1
    assert result.rank_constant == constant


@pytest.mark.parametrize("mode", ["hash", "missing_query", "duplicate_query", "unknown_query", "unknown_source", "bad_citation", "same_strategy", "answer_reference"])
def test_invalid_input_rejected_before_scoring(small, mode):
    left, right = runs(small, ["A", "B", "C"], ["B"])
    data = right.model_dump()
    if mode == "hash":
        data["dataset_sha256"] = "0" * 64
    elif mode == "missing_query":
        data["predictions"].pop()
    elif mode == "duplicate_query":
        data["predictions"][1] = data["predictions"][0]
    elif mode == "unknown_query":
        data["predictions"][0]["query_id"] = "unknown"
    elif mode == "unknown_source":
        data["predictions"][0]["ranked_source_ids"] = ["A", "B", "C", "unknown"]
    elif mode == "bad_citation":
        data["predictions"][0]["citations"] = [Citation(source_id="A", line=9999).model_dump()]
    elif mode == "same_strategy":
        data["strategy"] = left.strategy
    else:
        data["citation_origin"] = "answer_reference"
    with pytest.raises(ValueError):
        fuse_rrf(small, left, Run.model_validate(data))
