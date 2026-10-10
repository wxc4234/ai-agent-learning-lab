"""距离观测的数值、排序和失败分母；不连接数据库或模型。"""

from dataclasses import replace

import pytest

from app.services.workspace.files.code_distance_observation import observe_distances, distance_report
from app.services.workspace.files.code_retrieval_evaluation import Query, Prediction
from app.services.workspace.files.code_vector_search import CodeVectorHit


def sample(gold=None, distances=(0.2, 0.4, 0.8)):
    query = Query(id="q", query="question", relevant_source_ids=["a"] if gold is None else gold, rationale="fixture")
    ids = ["a", "b", "c"][:len(distances)]
    prediction = Prediction(query_id="q", status="ok", ranked_source_ids=ids, citations=[])
    hits = tuple(CodeVectorHit(i, d, {}) for i, d in enumerate(distances, 1))
    return query, prediction, hits


def test_ranks_labels_gap_and_statistics_do_not_change_prediction():
    query, prediction, hits = sample()
    before = prediction.model_dump()
    row = observe_distances(query, prediction, hits, k=2)
    assert [(c.source_id, c.rank, c.relevant, c.in_top_k) for c in row.candidates] == [
        ("a", 1, True, True), ("b", 2, False, True), ("c", 3, False, False)]
    assert row.nearest_distance == 0.2 and row.first_second_gap == pytest.approx(0.2)
    report = distance_report([row])
    assert report["groups"]["relevant_candidates"] == {"count": 1, "min": 0.2, "max": 0.2, "mean": 0.2}
    assert report["groups"]["irrelevant_candidates"]["mean"] == pytest.approx(0.6)
    assert report["groups"]["nearest_no_answer"] == {"count": 0, "min": None, "max": None, "mean": None}
    assert prediction.model_dump() == before
    other = observe_distances(query.model_copy(update={"relevant_source_ids": ["b"]}), prediction, hits)
    assert [c.distance for c in other.candidates] == [c.distance for c in row.candidates]
    assert [c.source_id for c in other.candidates] == prediction.ranked_source_ids


def test_no_answer_error_and_empty_are_distinct():
    query, prediction, hits = sample(gold=[])
    no_answer = observe_distances(query, prediction, hits)
    empty_query = query.model_copy(update={"id": "empty"})
    empty = observe_distances(empty_query, Prediction(query_id="empty", status="ok", ranked_source_ids=[], citations=[]), ())
    error_query = query.model_copy(update={"id": "error"})
    error = observe_distances(error_query, Prediction(query_id="error", status="error", ranked_source_ids=[], citations=[]), ())
    report = distance_report([no_answer, empty, error])
    assert not no_answer.answerable and all(not c.relevant for c in no_answer.candidates)
    assert empty.nearest_distance is error.nearest_distance is None
    assert empty.first_second_gap is error.first_second_gap is None
    assert report["error_count"] == report["empty_success_count"] == 1
    assert report["groups"]["nearest_no_answer"]["count"] == 1
    assert report["groups"]["irrelevant_candidates"]["count"] == 3


@pytest.mark.parametrize("distances", [(0, 0, 2), (0.5,)])
def test_ties_bounds_and_single_candidate(distances):
    row = observe_distances(*sample(distances=distances))
    assert row.first_second_gap == (0 if len(distances) > 1 else None)


@pytest.mark.parametrize("distances", [(float("nan"),), (float("inf"),), (-0.01,), (2.01,), (True,), ("0.2",), (0.8, 0.2)])
def test_invalid_distances_rejected(distances):
    with pytest.raises(ValueError, match="distance_observation_invalid"):
        observe_distances(*sample(distances=distances))


@pytest.mark.parametrize("mode", ["query", "count", "duplicate", "rank", "error_hits", "k"])
def test_mismatched_observation_rejected(mode):
    query, prediction, hits = sample()
    if mode == "query":
        query = query.model_copy(update={"id": "other"})
    elif mode == "count":
        hits = hits[:-1]
    elif mode == "duplicate":
        prediction = prediction.model_copy(update={"ranked_source_ids": ["a", "a", "b"]})
    elif mode == "rank":
        hits = (replace(hits[0], rank=2), *hits[1:])
    elif mode == "error_hits":
        prediction = Prediction(query_id="q", status="error", ranked_source_ids=[], citations=[])
    with pytest.raises(ValueError):
        observe_distances(query, prediction, hits, k=0 if mode == "k" else 3)


def test_duplicate_queries_rejected():
    row = observe_distances(*sample())
    with pytest.raises(ValueError, match="duplicate_distance_query"):
        distance_report([row, row])
