"""离线开发集校准/冻结及来源过滤；不把RRF分数视作概率。"""

from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
import json
import re
import unicodedata
from typing import Literal

from pydantic import Field, field_validator

from app.services.workspace.files.code_retrieval_evaluation import (
    Citation, Dataset, Prediction, Record, Run, dataset_digest, evaluate,
)

THRESHOLDS = (1, 2, 3, 4)
STRATEGY = "rrf-c60-v1"
RULE = "distinct-english-overlap-v1"
OBJECTIVE = "balanced-recall-loss-and-no-answer-fp-v1"


class Split(Record):
    role: Literal["development", "holdout"]
    dataset: Dataset


class FrozenPolicy(Record):
    schema_version: Literal[1]
    rule: Literal["distinct-english-overlap-v1"]
    input_strategy: Literal["rrf-c60-v1"]
    objective: Literal["balanced-recall-loss-and-no-answer-fp-v1"]
    corpus_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    development_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    min_shared_terms: int = Field(ge=1, le=4)
    evaluation_k: Literal[3]

    @field_validator("evaluation_k", mode="before")
    @classmethod
    def exact_k(cls, value):
        if type(value) is not int:
            raise ValueError("evaluation_k_requires_integer")
        return value


@dataclass(frozen=True)
class AbstentionResult:
    run: Run
    decisions: list[dict]


def corpus_digest(dataset: Dataset) -> str:
    raw = json.dumps([source.model_dump() for source in dataset.sources], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode()).hexdigest()


def validate_split(split: Split, verified_corpus: Dataset) -> None:
    if split.dataset.sources != verified_corpus.sources:
        raise ValueError("split_corpus_mismatch")
    normalized = [_normalized(q.query) for q in split.dataset.queries]
    if len(set(normalized)) != len(normalized):
        raise ValueError("duplicate_normalized_query")


def _normalized(query: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", query).casefold().split())


def validate_holdout(development: Split, holdout: Split, policy: FrozenPolicy) -> None:
    if development.role != "development" or holdout.role != "holdout":
        raise ValueError("invalid_split_roles")
    if policy.development_sha256 != dataset_digest(development.dataset):
        raise ValueError("policy_development_mismatch")
    if corpus_digest(development.dataset) != policy.corpus_sha256 or corpus_digest(holdout.dataset) != policy.corpus_sha256:
        raise ValueError("policy_corpus_mismatch")
    if {q.id for q in development.dataset.queries} & {q.id for q in holdout.dataset.queries}:
        raise ValueError("overlapping_query_id")
    if {_normalized(q.query) for q in development.dataset.queries} & {_normalized(q.query) for q in holdout.dataset.queries}:
        raise ValueError("overlapping_query_text")


def _terms(value: str) -> set[str]:
    return set(re.findall(r"[a-z][a-z0-9]*", value.casefold()))


def apply_policy(dataset: Dataset, texts: dict[str, str], run: Run, policy: FrozenPolicy) -> AbstentionResult:
    if run.strategy != policy.input_strategy or run.citation_origin != "retrieval_reference":
        raise ValueError("policy_strategy_mismatch")
    if corpus_digest(dataset) != policy.corpus_sha256 or set(texts) != {s.id for s in dataset.sources}:
        raise ValueError("policy_corpus_mismatch")
    evaluate(dataset, run, k=policy.evaluation_k)  # 只复用输入校验，gold不参与过滤。
    predictions = {p.query_id: p for p in run.predictions}
    sources = {s.id: s for s in dataset.sources}
    output, decisions = [], []
    for query in dataset.queries:
        prediction = predictions[query.id]
        unique = list(dict.fromkeys(prediction.ranked_source_ids))
        overlaps = {key: len(_terms(query.query) & _terms(texts[key])) for key in unique}
        kept = [key for key in unique if overlaps[key] >= policy.min_shared_terms]
        if prediction.status == "error":
            decision = "retrieval_error"
        elif not unique:
            decision = "no_candidates"
        elif not kept:
            decision = "abstained"
        else:
            decision = "retained"
        output.append(Prediction(query_id=query.id, status=prediction.status, ranked_source_ids=kept,
                                 citations=[Citation(source_id=kept[0], line=sources[kept[0]].start_line)] if kept else []))
        decisions.append({"query_id": query.id, "decision": decision, "input_count": len(unique), "retained_count": len(kept), "shared_terms": overlaps})
    return AbstentionResult(run=Run(schema_version=1, dataset_sha256=dataset_digest(dataset), strategy=f"{RULE}-t{policy.min_shared_terms}", citation_origin="retrieval_reference", predictions=output), decisions=decisions)


def refusal_metrics(dataset: Dataset, result: AbstentionResult) -> dict:
    report = evaluate(dataset, result.run, k=3)
    decisions = {d["query_id"]: d["decision"] for d in result.decisions}
    positives = [q for q in dataset.queries if q.relevant_source_ids]
    negatives = [q for q in dataset.queries if not q.relevant_source_ids]
    # 主动拒答、检索失败和原本无候选分开；失败不记作正确拒答。
    false_refusal = sum(decisions[q.id] == "abstained" for q in positives)
    positive_empty = sum(decisions[q.id] == "no_candidates" for q in positives)
    report["abstention"] = {
        "false_refusal_count": false_refusal,
        "false_refusal_rate": false_refusal / len(positives) if positives else None,
        "positive_no_candidates_count": positive_empty,
        "correct_abstention_count": sum(decisions[q.id] == "abstained" for q in negatives),
        "negative_no_candidates_count": sum(decisions[q.id] == "no_candidates" for q in negatives),
        "false_acceptance_count": sum(decisions[q.id] == "retained" for q in negatives),
        "false_acceptance_rate": sum(decisions[q.id] == "retained" for q in negatives) / len(negatives) if negatives else None,
        "retrieval_error_count": sum(d == "retrieval_error" for d in decisions.values()),
        "decisions": result.decisions,
    }
    return report


def calibrate(development: Split, texts: dict[str, str], run: Run) -> tuple[FrozenPolicy, list[dict]]:
    # API只接受development，不接受holdout；候选网格、目标及K在观察留出前固定。
    if development.role != "development":
        raise ValueError("calibration_requires_development")
    dataset = development.dataset
    positives = [q for q in dataset.queries if q.relevant_source_ids]
    negatives = [q for q in dataset.queries if not q.relevant_source_ids]
    if not positives or not negatives:
        raise ValueError("development_needs_both_classes")
    if any(p.status != "ok" for p in run.predictions):
        raise ValueError("development_retrieval_failed")
    trials, choices = [], []
    for threshold in THRESHOLDS:
        policy = FrozenPolicy(schema_version=1, rule=RULE, input_strategy=STRATEGY, objective=OBJECTIVE,
                              corpus_sha256=corpus_digest(dataset), development_sha256=dataset_digest(dataset),
                              min_shared_terms=threshold, evaluation_k=3)
        filtered = apply_policy(dataset, texts, run, policy)
        report = refusal_metrics(dataset, filtered)
        rows = {row["query_id"]: row for row in report["rows"]}
        recall = sum((Fraction(len(rows[q.id]["matched_source_ids"]), len(q.relevant_source_ids)) for q in positives), Fraction()) / len(positives)
        fpr = Fraction(report["no_answer_false_positive_count"], len(negatives))
        loss = (1 - recall + fpr) / 2
        # 先最小化平衡损失，再最大化正例召回，最后选择较低阈值；精确分数避免浮点并列。
        choices.append(((loss, -recall, threshold), policy))
        trials.append({"threshold": threshold, "balanced_loss": str(loss), "positive_recall": str(recall),
                       "no_answer_false_positive_rate": str(fpr), "false_refusal_count": report["abstention"]["false_refusal_count"]})
    return min(choices, key=lambda choice: choice[0])[1], trials
