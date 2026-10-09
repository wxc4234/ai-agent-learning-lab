"""离线来源级评测；只消费显式样例，不加载产品配置、数据库或模型客户端。"""

import ast
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_JSON_BYTES = 256 * 1024
MAX_SOURCE_BYTES = 64 * 1024


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @field_validator("schema_version", mode="before", check_fields=False)
    @classmethod
    def exact_version_type(cls, value):
        # Literal的相等比较不能让True/1.0冒充版本整数1。
        if type(value) is not int:
            raise ValueError("invalid_schema_version")
        return value


class Source(Record):
    # 来源固定到文件版本和完整顶层定义；不是动态项目的读取许可。
    id: str = Field(min_length=1, max_length=160)
    relative_path: str = Field(min_length=1, max_length=200)
    symbol: str = Field(min_length=1, max_length=100)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    file_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Query(Record):
    id: str = Field(min_length=1, max_length=80)
    query: str = Field(min_length=1, max_length=2000)
    # 空标注明确表示样例中无答案，不混入正例Recall/MRR的分母。
    relevant_source_ids: list[str] = Field(max_length=64)
    rationale: str = Field(min_length=1, max_length=1000)


class Dataset(Record):
    schema_version: Literal[1]
    dataset_id: str = Field(min_length=1, max_length=80)
    label_origin: Literal["coach_authored"]
    sources: list[Source] = Field(min_length=1, max_length=64)
    queries: list[Query] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def consistent(self):
        source_ids = [source.id for source in self.sources]
        query_ids = [query.id for query in self.queries]
        spans = [(s.relative_path, s.symbol) for s in self.sources]
        if len(set(source_ids)) != len(source_ids) or len(set(spans)) != len(spans):
            raise ValueError("duplicate_source")
        if len(set(query_ids)) != len(query_ids):
            raise ValueError("duplicate_query")
        for query in self.queries:
            gold = query.relevant_source_ids
            if not query.query.strip() or len(gold) != len(set(gold)) or not set(gold) <= set(source_ids):
                raise ValueError("invalid_gold")
        return self


class Citation(Record):
    source_id: str = Field(min_length=1, max_length=160)
    line: int = Field(ge=1)


class Prediction(Record):
    query_id: str = Field(min_length=1, max_length=80)
    status: Literal["ok", "error"]
    ranked_source_ids: list[str] = Field(max_length=256)
    citations: list[Citation] = Field(max_length=64)

    @model_validator(mode="after")
    def failure_has_no_results(self):
        if self.status == "error" and (self.ranked_source_ids or self.citations):
            raise ValueError("error_with_results")
        return self


class Run(Record):
    schema_version: Literal[1]
    dataset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    strategy: str = Field(min_length=1, max_length=100)
    # 基线引用是检索器给出的首项定位，不能伪装成模型回答的引用正确率。
    citation_origin: Literal["retrieval_reference", "answer_reference"]
    predictions: list[Prediction] = Field(min_length=1, max_length=64)


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def read_json(path: Path):
    with path.open("rb") as handle:
        raw = handle.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("json_too_large")
    return json.loads(raw, object_pairs_hook=_json_object)


def dataset_digest(dataset: Dataset) -> str:
    # 哈希包含题目、标注、版本和文件哈希，拒绝拿其他任务集结果来评分。
    raw = json.dumps(dataset.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


def _source_path(root: Path, relative: str) -> Path:
    if root.is_symlink():
        raise ValueError("symlink_source")
    parts = PurePosixPath(relative).parts
    if (
        not parts or relative != "/".join(parts) or PurePosixPath(relative).is_absolute()
        or any(part in (".", "..") or ":" in part for part in parts)
        or "\\" in relative or "\x00" in relative or not relative.endswith(".py")
    ):
        raise ValueError("invalid_source_path")
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("symlink_source")
    return path


def load_dataset(directory: Path) -> tuple[Dataset, dict[str, str]]:
    dataset = Dataset.model_validate(read_json(directory / "dataset.json"))
    texts = {}
    files: dict[str, bytes] = {}
    for source in dataset.sources:
        path = _source_path(directory / "corpus", source.relative_path)
        if source.relative_path not in files:
            with path.open("rb") as handle:
                files[source.relative_path] = handle.read(MAX_SOURCE_BYTES + 1)
        raw = files[source.relative_path]
        if len(raw) > MAX_SOURCE_BYTES or sha256(raw).hexdigest() != source.file_sha256:
            raise ValueError("source_version_mismatch")
        content = raw.decode("utf-8")
        # AST仅用于验证静态行号；不import、不执行样例或用户传入代码。
        definitions = [node for node in ast.parse(content).body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == source.symbol]
        if len(definitions) != 1 or (definitions[0].lineno, definitions[0].end_lineno) != (source.start_line, source.end_line):
            raise ValueError("source_span_mismatch")
        texts[source.id] = "".join(content.splitlines(keepends=True)[source.start_line - 1:source.end_line])
    return dataset, texts


def _terms(text: str) -> set[str]:
    # 故意透明且弱的英文标识符/单词基线，无停用词、同义词、中文分词或语义向量。
    return set(re.findall(r"[a-z][a-z0-9]*", text.casefold()))


def lexical_baseline(dataset: Dataset, texts: dict[str, str]) -> Run:
    if set(texts) != {source.id for source in dataset.sources}:
        raise ValueError("corpus_mismatch")
    predictions = []
    sources = {source.id: source for source in dataset.sources}
    for query in dataset.queries:
        terms = _terms(query.query)
        scores = {key: len(terms & _terms(value)) for key, value in texts.items()}
        # 并列固定来源ID顺序；零分不返回。检索函数不读取gold/rationale。
        ranked = sorted((key for key in scores if scores[key] > 0), key=lambda key: (-scores[key], key))
        predictions.append(Prediction(
            query_id=query.id, status="ok", ranked_source_ids=ranked,
            citations=[Citation(source_id=ranked[0], line=sources[ranked[0]].start_line)] if ranked else [],
        ))
    return Run(schema_version=1, dataset_sha256=dataset_digest(dataset), strategy="literal-term-overlap-v1", citation_origin="retrieval_reference", predictions=predictions)


def evaluate(dataset: Dataset, run: Run, *, k: int = 3) -> dict:
    if type(k) is not int or not 1 <= k <= 64:
        raise ValueError("invalid_k")
    if run.dataset_sha256 != dataset_digest(dataset):
        raise ValueError("dataset_mismatch")
    expected = {query.id for query in dataset.queries}
    counts = Counter(prediction.query_id for prediction in run.predictions)
    if set(counts) != expected or any(count != 1 for count in counts.values()):
        raise ValueError("query_set_mismatch")
    sources = {source.id: source for source in dataset.sources}
    predictions = {prediction.query_id: prediction for prediction in run.predictions}
    rows = []
    for query in dataset.queries:
        prediction = predictions[query.id]
        if not set(prediction.ranked_source_ids) <= set(sources):
            raise ValueError("unknown_ranked_source")
        # 同一来源可能由多个分块返回：保留首次排名后按来源去重，再截Top K。
        unique = list(dict.fromkeys(prediction.ranked_source_ids))
        top = unique[:k]
        gold = set(query.relevant_source_ids)
        matched = gold & set(top)
        citation_keys = [(citation.source_id, citation.line) for citation in prediction.citations]
        if len(set(citation_keys)) != len(citation_keys):
            raise ValueError("duplicate_citation")
        for citation in prediction.citations:
            source = sources.get(citation.source_id)
            if source is None or not source.start_line <= citation.line <= source.end_line:
                raise ValueError("invalid_citation_location")
        rows.append({
            "query_id": query.id, "status": prediction.status, "top_source_ids": top,
            "duplicate_count": len(prediction.ranked_source_ids) - len(unique),
            "relevant_count": len(gold), "matched_source_ids": sorted(matched),
            "recall_at_k": len(matched) / len(gold) if gold else None,
            "reciprocal_rank_at_k": next((1 / rank for rank, key in enumerate(top, 1) if key in gold), 0.0) if gold else None,
            "no_answer_false_positive": bool(top) if not gold and prediction.status == "ok" else None,
            "citation_count": len(prediction.citations),
            "relevant_citation_count": sum(c.source_id in gold for c in prediction.citations),
            "grounded_citation_count": sum(c.source_id in top for c in prediction.citations),
        })
    answerable = [row for row in rows if row["relevant_count"]]
    negatives = [row for row in rows if not row["relevant_count"]]
    citations = sum(row["citation_count"] for row in rows)
    # 失败正例仍以0进入宏平均；无答案失败不伪装成正确拒答，独立报告数量。
    return {
        "schema_version": 1, "dataset_id": dataset.dataset_id, "dataset_sha256": dataset_digest(dataset),
        "strategy": run.strategy, "citation_origin": run.citation_origin, "k": k,
        "query_count": len(rows), "answerable_count": len(answerable), "no_answer_count": len(negatives),
        "error_count": sum(row["status"] == "error" for row in rows),
        "recall_at_k": sum(row["recall_at_k"] for row in answerable) / len(answerable) if answerable else None,
        "mrr_at_k": sum(row["reciprocal_rank_at_k"] for row in answerable) / len(answerable) if answerable else None,
        "no_answer_false_positive_count": sum(row["no_answer_false_positive"] is True for row in negatives),
        "no_answer_error_count": sum(row["status"] == "error" for row in negatives),
        "citation_count": citations,
        "citation_relevance": sum(row["relevant_citation_count"] for row in rows) / citations if citations else None,
        "citation_grounding_at_k": sum(row["grounded_citation_count"] for row in rows) / citations if citations else None,
        "rows": rows,
        "limits": ["offline_authored_fixture", "source_level_dedup_before_k", "not_semantic_or_answer_quality", "no_latency_or_cost_measurement"],
    }
