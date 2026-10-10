"""已验证来源的余弦距离观测；标注只用于分析，不参与排序或拒答。"""

from dataclasses import asdict, dataclass
import math
from statistics import mean

from app.services.workspace.files.code_retrieval_evaluation import Prediction, Query
from app.services.workspace.files.code_vector_search import CodeVectorHit


@dataclass(frozen=True)
class CandidateDistance:
    source_id: str
    rank: int
    distance: float
    relevant: bool
    in_top_k: bool


@dataclass(frozen=True)
class QueryDistances:
    query_id: str
    status: str
    answerable: bool
    candidates: tuple[CandidateDistance, ...]
    # 差值仅表示前两名的距离间隔，不是回答正确概率。
    nearest_distance: float | None
    first_second_gap: float | None


def observe_distances(
    query: Query, prediction: Prediction, hits: tuple[CodeVectorHit, ...], *, k: int = 3,
) -> QueryDistances:
    """调用者先完成完整来源/空间映射，再按相同顺序绑定距离。"""
    if type(k) is not int or k < 1 or prediction.query_id != query.id:
        raise ValueError("distance_observation_mismatch")
    ids = prediction.ranked_source_ids
    if len(hits) != len(ids) or len(ids) != len(set(ids)):
        raise ValueError("distance_observation_mismatch")
    if prediction.status == "error" and hits:
        raise ValueError("distance_observation_mismatch")
    gold = set(query.relevant_source_ids)
    candidates = []
    previous = -1.0
    for rank, (source_id, hit) in enumerate(zip(ids, hits, strict=True), 1):
        distance = hit.distance
        if (type(distance) not in (int, float) or not math.isfinite(distance)
                or not 0 <= distance <= 2 or distance < previous or hit.rank != rank):
            raise ValueError("distance_observation_invalid")
        previous = distance
        candidates.append(CandidateDistance(source_id, rank, float(distance), source_id in gold, rank <= k))
    return QueryDistances(query.id, prediction.status, bool(gold), tuple(candidates),
                          candidates[0].distance if candidates else None,
                          candidates[1].distance - candidates[0].distance if len(candidates) >= 2 else None)


def distance_report(rows: list[QueryDistances]) -> dict:
    """错误/空结果没有可观测距离；不能用0把它们伪装成高度相似。"""
    if len({row.query_id for row in rows}) != len(rows):
        raise ValueError("duplicate_distance_query")

    def summary(values: list[float]) -> dict:
        return {"count": len(values), "min": min(values) if values else None,
                "max": max(values) if values else None, "mean": mean(values) if values else None}

    groups = {
        "nearest_answerable": [row.nearest_distance for row in rows if row.answerable and row.nearest_distance is not None],
        "nearest_no_answer": [row.nearest_distance for row in rows if not row.answerable and row.nearest_distance is not None],
        "relevant_candidates": [c.distance for row in rows for c in row.candidates if c.relevant],
        "irrelevant_candidates": [c.distance for row in rows for c in row.candidates if not c.relevant],
    }
    return {"schema_version": 1, "metric": "cosine_distance", "direction": "lower_is_closer",
            "scope": "same configured model space; all returned source candidates",
            "error_count": sum(row.status == "error" for row in rows),
            "empty_success_count": sum(row.status == "ok" and not row.candidates for row in rows),
            "rows": [dict(asdict(row), candidates=[asdict(c) for c in row.candidates]) for row in rows],
            "groups": {name: summary(values) for name, values in groups.items()},
            "limits": ["distance is not confidence", "descriptive only; no threshold selected",
                       "candidate samples from the same query are not independent",
                       "labels never change ranking or provider requests"]}
