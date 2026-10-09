"""来源级双路RRF，仅用于离线评测，不改变产品检索策略。"""

from dataclasses import dataclass
from fractions import Fraction

from app.services.workspace.files.code_retrieval_evaluation import (
    Citation, Dataset, Prediction, Run, dataset_digest, evaluate,
)


@dataclass(frozen=True)
class FusionResult:
    run: Run
    rank_constant: int
    # 保留两路排名与精确分数，解释缺失来源/并列；不把融合分数称作概率。
    diagnostics: list[dict]
    failure_policy: str = "any_input_error_fails_query"


def fuse_rrf(dataset: Dataset, left: Run, right: Run, *, rank_constant: int = 60) -> FusionResult:
    if type(rank_constant) is not int or not 1 <= rank_constant <= 10000:
        raise ValueError("invalid_rrf_constant")
    if left.strategy == right.strategy:
        raise ValueError("duplicate_rrf_strategy")
    if any(run.citation_origin != "retrieval_reference" for run in (left, right)):
        raise ValueError("rrf_requires_retrieval_rankings")
    # 复用既有全集/版本/所有候选及引用位置校验；评分返回值不参与融合。
    # 即使坏候选位于K以后也整次拒绝，不能靠截断掩盖输入错配。
    for run in (left, right):
        evaluate(dataset, run)
    inputs = {run.strategy: {p.query_id: p for p in run.predictions} for run in (left, right)}
    strategies = sorted(inputs)
    source_by_id = {source.id: source for source in dataset.sources}
    predictions, diagnostics = [], []
    for query in dataset.queries:
        current = {name: inputs[name][query.id] for name in strategies}
        failed = [name for name in strategies if current[name].status == "error"]
        if failed:
            predictions.append(Prediction(query_id=query.id, status="error", ranked_source_ids=[], citations=[]))
            diagnostics.append({"query_id": query.id, "failed_strategies": failed, "candidates": []})
            continue
        # 去重发生在赋予名次前，与来源级评测口径一致；缺失项不补虚构末位。
        ranks = {name: {key: rank for rank, key in enumerate(dict.fromkeys(current[name].ranked_source_ids), 1)} for name in strategies}
        candidates = set().union(*(set(values) for values in ranks.values()))
        scores = {key: sum((Fraction(1, rank_constant + ranks[name][key]) for name in strategies if key in ranks[name]), Fraction()) for key in candidates}
        ordered = sorted(candidates, key=lambda key: (-scores[key], key))
        citations = [Citation(source_id=ordered[0], line=source_by_id[ordered[0]].start_line)] if ordered else []
        predictions.append(Prediction(query_id=query.id, status="ok", ranked_source_ids=ordered, citations=citations))
        diagnostics.append({
            "query_id": query.id, "failed_strategies": [],
            "candidates": [{"source_id": key, "ranks": {name: ranks[name].get(key) for name in strategies},
                            "score": float(scores[key]), "score_fraction": str(scores[key])} for key in ordered],
        })
    # 输出完整来源并集；统一评测器在融合之后按同一个K裁剪，不能提前只取每路Top K。
    return FusionResult(
        run=Run(schema_version=1, dataset_sha256=dataset_digest(dataset), strategy=f"rrf-c{rank_constant}-v1", citation_origin="retrieval_reference", predictions=predictions),
        rank_constant=rank_constant, diagnostics=diagnostics,
    )
