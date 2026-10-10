"""复用授权生成/查询的离线执行；调用者必须绑定隔离学习语料目录。"""

from hashlib import sha256

from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.model.embedding_config import EmbeddingError
from app.services.workspace.files.code_batch_generation import generate_and_save_code_batch
from app.services.workspace.files.code_query_search import search_code_query
from app.services.workspace.files.code_vector_search import CodeVectorSearchError, MAX_TOP_K
from app.services.workspace.files.code_evaluation_mapping import map_recall
from app.services.workspace.files.code_distance_observation import observe_distances, distance_report
from app.services.workspace.files.code_evaluation_observation import Observation
from app.services.workspace.files.code_provider_evaluation import ProviderPlan, SendBudget
from app.services.workspace.files.code_retrieval_evaluation import (
    Prediction, Run, dataset_digest, evaluate, lexical_baseline,
)
from app.services.workspace.files.code_rrf_evaluation import fuse_rrf


async def run_evaluation(plan: ProviderPlan, *, scope: dict, transport_factory) -> dict:
    dataset, texts = plan.load_dataset()
    budget = SendBudget(plan)
    observation = Observation()
    with observation.span("build", "build", "end_to_end"):
        saved = await generate_and_save_code_batch(
            **scope, config=plan.config,
            transport=observation.transport("build", "build", transport_factory(budget)),
        )
        observation.accept_usage("build", "build", saved)
        if saved.response_model != plan.response_model or saved.batch.chunk_count != len(dataset.sources):
            raise ValueError("evaluation_build_space_mismatch")
    predictions = []
    distances = []
    for query in dataset.queries:
        observation.entry("query", query.id)
        hits = ()
        try:
            # 保留真实授权→事务外生成→重新授权召回；此处计整个查询，不伪称纯HTTP延迟。
            with observation.span("query", query.id, "end_to_end"):
                result = await search_code_query(
                    query.query, **scope, batch_id=saved.batch.batch_id,
                    config=plan.config, response_model=plan.response_model, top_k=MAX_TOP_K,
                    transport=observation.transport("query", query.id, transport_factory(budget)),
                )
                observation.accept_usage("query", query.id, result)
                if result.query_sha256 != sha256(query.query.encode()).hexdigest():
                    raise ValueError("evaluation_query_mismatch")
                prediction = map_recall(dataset, texts, result.recall,
                                        workspace_id=scope["workspace_id"], task_id=scope["task_id"],
                                        batch_id=saved.batch.batch_id, query_id=query.id,
                                        config=plan.config, response_model=plan.response_model)
                hits = result.recall.hits
        except (EmbeddingError, WorkspaceNotAccessibleError, CodeVectorSearchError):
            prediction = Prediction(query_id=query.id, status="error", ranked_source_ids=[], citations=[])
        # HTTP和数据库事务均已退出，来源验证完成后只做内存观测。
        distances.append(observe_distances(query, prediction, hits))
        predictions.append(prediction)
    vector = Run(schema_version=1, dataset_sha256=dataset_digest(dataset), strategy="configured-provider-v1",
                 citation_origin="retrieval_reference", predictions=predictions)
    with observation.span("evaluation", "dataset", "literal"):
        literal = lexical_baseline(dataset, texts)
    with observation.span("evaluation", "dataset", "fusion"):
        fused = fuse_rrf(dataset, literal, vector).run
    return {"schema_version": 1, "manifest": budget.manifest,
            "requests": budget.requests, "request_body_bytes": budget.bytes,
            "literal": evaluate(dataset, literal), "vector": evaluate(dataset, vector),
            "rrf": evaluate(dataset, fused), "observation": observation.report(),
            "distance_observation": distance_report(distances),
            "limitations": ["no calibrated provider abstention", "cost unknown",
                            "query usage unknown when failure occurs before combined result"]}
