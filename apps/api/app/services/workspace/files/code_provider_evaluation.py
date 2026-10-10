"""固定学习语料的离线评测计划和发送边界；不接受任意项目路径。"""

from collections import Counter
from dataclasses import dataclass, field
import json
from typing import Literal
from hashlib import sha256
from pathlib import Path

import httpx

from app.services.model.embedding_config import EmbeddingConfig
from app.services.workspace.files.code_abstention_evaluation import Split, validate_split, _normalized
from app.services.workspace.files.code_retrieval_evaluation import dataset_digest, load_dataset

FIXTURE = Path(__file__).resolve().parents[4] / "evaluations/code_retrieval/v1"


@dataclass(frozen=True)
class ProviderPlan:
    config: EmbeddingConfig = field(repr=False)
    response_model: str
    max_requests: int = 9
    max_request_bytes: int = 32 * 1024
    max_total_bytes: int = 128 * 1024
    dataset_role: Literal["baseline", "distance-development", "distance-holdout"] = "baseline"

    def __post_init__(self):
        if self.dataset_role not in ("baseline", "distance-development", "distance-holdout"):
            raise ValueError("evaluation_dataset_invalid")
        EmbeddingConfig.validate_model(self.response_model)
        for value, limit in ((self.max_requests, 32), (self.max_request_bytes, 256 * 1024),
                             (self.max_total_bytes, 1024 * 1024)):
            if type(value) is not int or not 1 <= value <= limit:
                raise ValueError("evaluation_budget_invalid")

    def load_dataset(self):
        dataset, texts = load_dataset(FIXTURE)
        if self.dataset_role == "distance-development":
            # 仅允许仓库固定开发题；不接受任意路径，不读取留出题。
            split = Split.model_validate_json((FIXTURE.parent / "distance-v1/development.json").read_bytes())
            if split.role != "development":
                raise ValueError("calibration_requires_development")
            validate_split(split, dataset)
            if ({q.id for q in dataset.queries} & {q.id for q in split.dataset.queries}
                    or {_normalized(q.query) for q in dataset.queries} & {_normalized(q.query) for q in split.dataset.queries}):
                raise ValueError("development_overlaps_observed_queries")
            dataset = split.dataset
        if self.dataset_role == "distance-holdout":
            from app.services.workspace.files.code_distance_holdout import load_frozen_holdout
            split, _ = load_frozen_holdout(FIXTURE.parent / "distance-v1", dataset)
            dataset = split.dataset
        return dataset, texts

    def preflight(self) -> dict:
        dataset, texts = self.load_dataset()
        # 当前固定语料8个定义恰好一批；序列化预算按HTTPX真实JSON格式计量。
        inputs = [list(texts.values())] + [[q.query] for q in dataset.queries]
        sizes = [len(httpx.Request("POST", "https://evaluation.invalid", json=self.payload(items)).content) for items in inputs]
        if len(sizes) > self.max_requests or max(sizes) > self.max_request_bytes or sum(sizes) > self.max_total_bytes:
            raise ValueError("evaluation_budget_exceeded")
        manifest = {"config_sha256": sha256(self.config.model_dump_json().encode()).hexdigest(),
                "dataset_sha256": dataset_digest(dataset), "source_count": len(texts),
                "query_count": len(dataset.queries), "dataset_role": self.dataset_role, "planned_requests": len(sizes),
                "planned_body_bytes": sum(sizes), "dimensions": self.config.dimensions,
                "requested_model": self.config.model, "expected_response_model": self.response_model,
                "cost": None, "cost_state": "unknown"}
        if self.dataset_role == "distance-holdout":
            from app.services.workspace.files.code_distance_holdout import load_frozen_holdout, validate_model_binding
            baseline, _ = load_dataset(FIXTURE)
            _, policy = load_frozen_holdout(FIXTURE.parent / "distance-v1", baseline)
            validate_model_binding(policy, manifest)
        return manifest

    def payload(self, inputs: list[str]) -> dict:
        result = {"model": self.config.model, "input": inputs, "encoding_format": "float"}
        if self.config.request_dimensions:
            result["dimensions"] = self.config.dimensions
        return result


class SendBudget:
    def __init__(self, plan: ProviderPlan):
        self.plan = plan
        self.manifest = plan.preflight()
        dataset, texts = plan.load_dataset()
        # 计数器限制每段固定文本最多发送一次；拒绝新增正文和重复重放。
        self.remaining = Counter([*texts.values(), *(q.query for q in dataset.queries)])
        self.requests = 0
        self.bytes = 0

    def check(self, request: httpx.Request) -> None:
        try:
            body = json.loads(request.content)
            inputs = body["input"]
            if type(inputs) is not list or not inputs or any(type(item) is not str for item in inputs):
                raise ValueError()
            if request.method != "POST" or request.url != httpx.URL(self.plan.config.base_url + "/embeddings"):
                raise ValueError()
            if body != self.plan.payload(inputs):
                raise ValueError()
            counts = Counter(inputs)
            if any(count > self.remaining[item] for item, count in counts.items()):
                raise ValueError()
            size = len(request.content)
            if (self.requests >= self.plan.max_requests or size > self.plan.max_request_bytes
                    or self.bytes + size > self.plan.max_total_bytes):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise ValueError("evaluation_send_rejected") from None
        # 发送前扣减：未知失败不退款、不自动重试。
        self.remaining.subtract(counts)
        self.requests += 1
        self.bytes += size

    def transport(self, *, allow_network: bool = False, mock: httpx.MockTransport | None = None):
        if mock is None and not allow_network:
            raise ValueError("evaluation_network_not_enabled")
        if mock is not None and type(mock) is not httpx.MockTransport:
            raise ValueError("evaluation_requires_mock_transport")
        budget = self

        class Guarded(httpx.AsyncBaseTransport):
            def __init__(self):
                self.inner = mock if mock is not None else httpx.AsyncHTTPTransport(retries=0, trust_env=False)

            async def handle_async_request(self, request):
                budget.check(request)
                return await self.inner.handle_async_request(request)

            async def aclose(self):
                await self.inner.aclose()

        return Guarded()
