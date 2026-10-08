"""有界生成一条查询向量；调用方负责选择文本及发送许可。"""

import asyncio
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import httpx

from app.services.model.code_embeddings import (
    MAX_INPUT_BYTES,
    MAX_INPUT_CHARS,
    MAX_RESPONSE_BYTES,
    _invalid_response,
    _parse_response,
    code_embedding_space_id,
)
from app.services.model.embedding_config import EmbeddingConfig, EmbeddingError


@dataclass(frozen=True)
class QueryEmbedding:
    # 摘要对应实际发送的UTF-8文本；结果不重复保存查询正文。
    query_sha256: str
    vector: tuple[float, ...]
    requested_model: str
    response_model: str
    dimensions: int
    embedding_space_id: str

    # 缺失用量保持未知，不能按零费用处理。
    prompt_tokens: int | None
    total_tokens: int | None

    # 成功结果对应一次应用层请求，不证明供应商计费或内部执行次数。
    request_count: int = 1
    source: str = "query_embedding"


def _prepare_query(query: str) -> str:
    if type(query) is not str or not query.strip() or "\x00" in query:
        raise EmbeddingError(
            "embedding_query_invalid",
            "查询文本必须是非空文本且不能包含空字符",
        )

    try:
        raw = query.encode("utf-8")
    except UnicodeError:
        raise EmbeddingError(
            "embedding_query_invalid",
            "查询文本无法编码为UTF-8",
        ) from None

    # 沿用现有单输入预算；字符/字节数量不是模型Token数量。
    if len(query) > MAX_INPUT_CHARS or len(raw) > MAX_INPUT_BYTES:
        raise EmbeddingError(
            "embedding_query_budget_exceeded",
            "查询文本超过字符或字节预算",
        )

    # 不strip或改写正文，摘要与实际发送的内容保持一致。
    return sha256(raw).hexdigest()


async def generate_query_embedding(
    query: str,
    *,
    config: EmbeddingConfig,
    transport: httpx.AsyncBaseTransport | None = None,
) -> QueryEmbedding:
    """显式消费查询与独立配置；模型请求不持有数据库事务。"""
    query_sha256 = _prepare_query(query)

    payload: dict[str, Any] = {
        "model": config.model,
        "input": [query],
        "encoding_format": "float",
    }
    if config.request_dimensions:
        payload["dimensions"] = config.dimensions

    try:
        # 总等待预算覆盖请求、响应读取及客户端收尾。
        # HTTP超时另外限制单次I/O等待；本函数没有数据库Session。
        async with asyncio.timeout(config.timeout_seconds):
            async with httpx.AsyncClient(
                transport=transport,
                timeout=config.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                # 只发送选定正文和模型参数，不附加项目路径或用户身份。
                async with client.stream(
                    "POST",
                    config.base_url + "/embeddings",
                    json=payload,
                    headers={
                        "Authorization": (
                            "Bearer " + config.api_key.get_secret_value()
                        ),
                        "Accept-Encoding": "identity",
                    },
                ) as response:
                    if not response.is_success:
                        raise EmbeddingError(
                            "embedding_request_failed",
                            "Embedding请求失败，未返回查询向量",
                        )

                    if (
                        response.headers.get("content-encoding", "identity").lower()
                        != "identity"
                    ):
                        raise _invalid_response()

                    media = (
                        response.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                    )
                    if media != "application/json" and not (
                        media.startswith("application/") and media.endswith("+json")
                    ):
                        raise _invalid_response()

                    # 分段读取并限制完整响应，不能无界调用response.json()。
                    buffer = bytearray()
                    async for piece in response.aiter_bytes(chunk_size=64 * 1024):
                        if len(buffer) + len(piece) > MAX_RESPONSE_BYTES:
                            raise EmbeddingError(
                                "embedding_response_too_large",
                                "Embedding响应超过读取预算",
                            )
                        buffer.extend(piece)

                    # count=1要求唯一结果且index=0。
                    # 共用解析器检查重复JSON键、报告模型、维度、数值和用量。
                    model, vectors, prompt, total = _parse_response(
                        bytes(buffer),
                        count=1,
                        dimensions=config.dimensions,
                    )

                result = QueryEmbedding(
                    query_sha256=query_sha256,
                    vector=vectors[0],
                    requested_model=config.model,
                    response_model=model,
                    dimensions=config.dimensions,
                    embedding_space_id=code_embedding_space_id(config, model),
                    prompt_tokens=prompt,
                    total_tokens=total,
                )

    except (TimeoutError, httpx.TimeoutException):
        raise EmbeddingError(
            "embedding_timeout",
            "Embedding请求超时，未返回查询向量",
        ) from None
    except EmbeddingError:
        raise
    except httpx.HTTPError:
        raise EmbeddingError(
            "embedding_request_failed",
            "Embedding请求失败，未返回查询向量",
        ) from None
    except Exception:  # noqa: BLE001 -- 客户端边界隐藏凭证与正文；取消继续传播
        raise EmbeddingError(
            "embedding_request_failed",
            "Embedding请求失败，未返回查询向量",
        ) from None

    # 成功退出客户端上下文后才返回；取消不是普通Exception，继续向上传播。
    return result
