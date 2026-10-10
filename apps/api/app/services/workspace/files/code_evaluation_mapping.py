"""供应商无关的完整来源映射；模型空间必须由可信宿主显式提供。"""

from hashlib import sha256

from app.services.model.embedding_config import EmbeddingConfig
from app.services.model.code_embeddings import code_embedding_space_id
from app.services.workspace.files.code_retrieval_evaluation import Citation, Dataset, Prediction
from app.services.workspace.files.code_vector_search import CodeVectorSearchResult, MAX_TOP_K


def map_recall(
    dataset: Dataset, texts: dict[str, str], result: CodeVectorSearchResult, *,
    workspace_id: str, task_id: str, batch_id: str, query_id: str, config: EmbeddingConfig, response_model: str,
) -> Prediction:
    """v1只接完整单块定义；未来分片策略须另行明确聚合，不能静默丢候选。"""
    metadata = result.metadata
    expected_files = {s.relative_path: s.file_sha256 for s in dataset.sources}
    files = metadata.get("files", [])
    if (
        set(texts) != {s.id for s in dataset.sources}
        or result.batch_id != batch_id or result.top_k != MAX_TOP_K
        or result.dimensions != config.dimensions or result.space_id != code_embedding_space_id(config, response_model)
        or metadata.get("workspace_id") != workspace_id or metadata.get("task_id") != task_id
        or metadata.get("dimensions") != config.dimensions or metadata.get("embedding_space_id") != result.space_id
        or metadata.get("requested_model") != config.model or metadata.get("response_model") != response_model
        or metadata.get("truncated") is not False or metadata.get("incomplete_reasons") != []
        or len(files) != len(expected_files)
        or {file["relative_path"]: file["sha256"] for file in files} != expected_files
        or result.batch_chunk_count != len(dataset.sources)
        or result.searchable_chunk_count != len(dataset.sources)
        or result.excluded_zero_chunk_count != 0 or result.omitted_by_top_k != 0
        or len(result.hits) != len(dataset.sources)
    ):
        raise ValueError("evaluation_snapshot_mismatch")
    sources = {(s.relative_path, s.symbol): s for s in dataset.sources}
    ranked = []
    for rank, hit in enumerate(result.hits, 1):
        chunk = hit.chunk
        symbol = chunk["symbol"]
        source = sources.get((symbol["relative_path"], symbol["qualified_name"]))
        if source is None:
            raise ValueError("evaluation_unknown_source")
        expected_text = texts[source.id]
        if (
            hit.rank != rank or source.id in ranked
            or symbol["sha256"] != source.file_sha256
            or (symbol["start_line"], symbol["end_line"]) != (source.start_line, source.end_line)
            or symbol["definition_line"] != source.start_line or symbol["name"] != source.symbol
            or chunk["start_line"] != source.start_line or chunk["start_column"] != 1
            or chunk["end_line"] != source.start_line + expected_text.count("\n")
            or chunk["end_column"] != len(expected_text.rsplit("\n", 1)[-1]) + 1
            or chunk["text"] != expected_text
            or chunk["text_sha256"] != sha256(expected_text.encode()).hexdigest()
            or chunk["part_index"] != 1 or chunk["part_count"] != 1 or chunk["split_reasons"] != []
        ):
            raise ValueError("evaluation_source_mismatch")
        ranked.append(source.id)
    first = next(s for s in dataset.sources if s.id == ranked[0])
    return Prediction(query_id=query_id, status="ok", ranked_source_ids=ranked, citations=[Citation(source_id=first.id, line=first.start_line)])

