"""有界生成当前内存分块的向量；不授予文件访问权、不读磁盘或写索引。"""

import asyncio
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
import re
from typing import Any

import httpx

from app.services.model.embedding_config import (
    EmbeddingConfig,
    EmbeddingError,
    load_embedding_config,
)
from app.services.workspace.files.code_inventory import CodeFile
from app.services.workspace.files.python_chunks import (
    PythonCodeChunk,
    PythonCodeChunks,
    ChunkIncompleteReason,
)


# 字符/字节限制控制本机输入资源，不冒称供应商模型的Token限制。
MAX_EMBEDDING_INPUTS = 20
MAX_INPUT_CHARS = 2000
MAX_INPUT_BYTES = 4096
MAX_TOTAL_INPUT_BYTES = 80 * 1024
MAX_REQUEST_INPUTS = 8
MAX_REQUEST_INPUT_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_EMBEDDING_RESULT_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class EmbeddedCodeChunk:
    chunk: PythonCodeChunk
    vector: tuple[float, ...]


@dataclass(frozen=True)
class CodeEmbeddings:
    workspace_id: str
    task_id: str
    files: tuple[CodeFile, ...]
    embeddings: tuple[EmbeddedCodeChunk, ...]
    requested_model: str
    # 供应商可以报告模型别名对应的版本，但同次所有批次必须报告一致版本。
    response_model: str | None
    dimensions: int
    embedding_space_id: str | None
    request_count: int
    prompt_tokens: int | None
    total_tokens: int | None
    truncated: bool
    incomplete_reasons: tuple[ChunkIncompleteReason, ...]
    chunk_strategy: str
    chunk_policy: str
    chunk_parser: str
    source: str = "python_code_embeddings"
    content_trust: str = "untrusted_project_content"


def code_embedding_space_id(config: EmbeddingConfig, response_model: str) -> str:
    """生成和入库共用空间身份；密钥轮换不改变空间，模型版本变化则隔离。"""
    identity = [
        "openai_compatible_float_v1",
        config.base_url,
        config.model,
        response_model,
        config.dimensions,
        config.request_dimensions,
    ]
    return sha256(
        json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _invalid_response() -> EmbeddingError:
    return EmbeddingError(
        "embedding_response_invalid", "Embedding响应未通过数量、索引、维度或数值校验"
    )


def _prepare(source: PythonCodeChunks) -> list[tuple[PythonCodeChunk, ...]]:
    if len(source.chunks) > MAX_EMBEDDING_INPUTS:
        raise EmbeddingError("embedding_input_budget_exceeded", "Embedding输入超出预算")
    files = {file.relative_path: file.sha256 for file in source.files}
    seen: set[str] = set()
    total = 0
    # 先校验所有输入再发送第一批；不能已付费后才发现末尾输入无效。
    for chunk in source.chunks:
        if (
            not isinstance(chunk.text, str)
            or not chunk.text
            or not isinstance(chunk.chunk_id, str)
            or re.fullmatch(r"[a-f0-9]{64}", chunk.chunk_id) is None
            or chunk.chunk_id in seen
        ):
            raise EmbeddingError(
                "embedding_input_invalid", "Embedding分块内容或来源无效"
            )
        try:
            raw = chunk.text.encode("utf-8")
        except UnicodeError:
            raise EmbeddingError(
                "embedding_input_invalid", "Embedding分块内容或来源无效"
            ) from None
        if len(chunk.text) > MAX_INPUT_CHARS or len(raw) > MAX_INPUT_BYTES:
            raise EmbeddingError(
                "embedding_input_budget_exceeded", "Embedding输入超出预算"
            )
        total += len(raw)
        if total > MAX_TOTAL_INPUT_BYTES:
            raise EmbeddingError(
                "embedding_input_budget_exceeded", "Embedding输入超出预算"
            )
        if (
            sha256(raw).hexdigest() != chunk.text_sha256
            or files.get(chunk.symbol.relative_path) != chunk.symbol.sha256
        ):
            raise EmbeddingError(
                "embedding_input_invalid", "Embedding分块内容或来源无效"
            )
        seen.add(chunk.chunk_id)
    batches: list[tuple[PythonCodeChunk, ...]] = []
    current: list[PythonCodeChunk] = []
    size = 0
    for chunk in source.chunks:
        count = len(chunk.text.encode("utf-8"))
        if current and (
            len(current) == MAX_REQUEST_INPUTS or size + count > MAX_REQUEST_INPUT_BYTES
        ):
            batches.append(tuple(current))
            current, size = [], 0
        current.append(chunk)
        size += count
    if current:
        batches.append(tuple(current))
    return batches


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError()


def _parse_response(
    raw: bytes, count: int, dimensions: int
) -> tuple[str, tuple[tuple[float, ...], ...], int | None, int | None]:
    try:
        body = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant
        )
        if not isinstance(body, dict) or body.get("object") != "list":
            raise ValueError()
        model = body.get("model")
        if (
            not isinstance(model, str)
            or not model
            or len(model) > 256
            or len(model.encode("utf-8")) > 1024
            or model != model.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in model)
        ):
            raise ValueError()
        data = body.get("data")
        if not isinstance(data, list) or len(data) != count:
            raise ValueError()
        ordered: dict[int, tuple[float, ...]] = {}
        for item in data:
            if not isinstance(item, dict) or item.get("object") != "embedding":
                raise ValueError()
            index = item.get("index")
            if type(index) is not int or not 0 <= index < count or index in ordered:
                raise ValueError()
            vector = item.get("embedding")
            if not isinstance(vector, list) or len(vector) != dimensions:
                raise ValueError()
            converted = []
            for number in vector:
                if type(number) not in (int, float):
                    raise ValueError()
                value = float(number)
                if not math.isfinite(value):
                    raise ValueError()
                converted.append(value)
            ordered[index] = tuple(converted)
        usage = body.get("usage")
        prompt = total = None
        if usage is not None:
            if not isinstance(usage, dict):
                raise ValueError()
            prompt, total = usage.get("prompt_tokens"), usage.get("total_tokens")
            if (
                type(prompt) is not int
                or type(total) is not int
                or not 0 <= prompt <= total
            ):
                raise ValueError()
        return model, tuple(ordered[index] for index in range(count)), prompt, total
    except (ValueError, UnicodeError, OverflowError, RecursionError, MemoryError):
        raise _invalid_response() from None


async def generate_code_embeddings(
    source: PythonCodeChunks,
    *,
    config: EmbeddingConfig | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CodeEmbeddings:
    """可信调用方显式选择内存正文；旧分块不是当前授权或发送许可。"""

    active = config if config is not None else load_embedding_config()
    batches = _prepare(source)
    embeddings: list[EmbeddedCodeChunk] = []
    response_model: str | None = None
    prompt_sum = total_sum = 0
    usage_complete = True
    if batches:
        try:
            # 总墙钟超时覆盖所有批次和资源收尾；HTTP超时另限单次I/O等待。
            async with asyncio.timeout(active.timeout_seconds):
                async with httpx.AsyncClient(
                    transport=transport,
                    timeout=active.timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    for batch in batches:
                        payload: dict[str, Any] = {
                            "model": active.model,
                            "input": [chunk.text for chunk in batch],
                            "encoding_format": "float",
                        }
                        if active.request_dimensions:
                            payload["dimensions"] = active.dimensions
                        # 不发送路径、ID、来源或用户身份；Key仅在请求认证头中解封。
                        async with client.stream(
                            "POST",
                            active.base_url + "/embeddings",
                            json=payload,
                            headers={
                                "Authorization": "Bearer "
                                + active.api_key.get_secret_value(),
                                "Accept-Encoding": "identity",
                            },
                        ) as response:
                            if not response.is_success:
                                raise EmbeddingError(
                                    "embedding_request_failed",
                                    "Embedding请求失败，未返回向量结果",
                                )
                            if (
                                response.headers.get(
                                    "content-encoding", "identity"
                                ).lower()
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
                                media.startswith("application/")
                                and media.endswith("+json")
                            ):
                                raise _invalid_response()
                            buffer = bytearray()
                            async for piece in response.aiter_bytes(
                                chunk_size=64 * 1024
                            ):
                                if len(buffer) + len(piece) > MAX_RESPONSE_BYTES:
                                    raise EmbeddingError(
                                        "embedding_response_too_large",
                                        "Embedding响应超过读取预算",
                                    )
                                buffer.extend(piece)
                            model, vectors, prompt, total = _parse_response(
                                bytes(buffer), len(batch), active.dimensions
                            )
                        if response_model is not None and response_model != model:
                            raise _invalid_response()
                        response_model = model
                        embeddings.extend(
                            EmbeddedCodeChunk(chunk, vector)
                            for chunk, vector in zip(batch, vectors, strict=True)
                        )
                        if prompt is None or total is None:
                            usage_complete = False
                        else:
                            prompt_sum += prompt
                            total_sum += total
        except (TimeoutError, httpx.TimeoutException):
            raise EmbeddingError(
                "embedding_timeout", "Embedding请求超时，未返回向量结果"
            ) from None
        except httpx.HTTPError:
            raise EmbeddingError(
                "embedding_request_failed", "Embedding请求失败，未返回向量结果"
            ) from None
        except EmbeddingError:
            raise
        except Exception:  # noqa: BLE001 -- 客户端边界统一隐藏未知异常中的密钥和正文，取消继续传播
            # 未知客户端/供应商异常也不反射认证头或正文；取消是BaseException，继续传播。
            raise EmbeddingError(
                "embedding_request_failed", "Embedding请求失败，未返回向量结果"
            ) from None
    result = CodeEmbeddings(
        workspace_id=source.workspace_id,
        task_id=source.task_id,
        files=source.files,
        embeddings=tuple(embeddings),
        requested_model=active.model,
        response_model=response_model,
        dimensions=active.dimensions,
        # 同维度或同名模型不保证同一向量空间；区分供应商、请求配置与报告版本。
        embedding_space_id=code_embedding_space_id(active, response_model)
        if response_model is not None
        else None,
        request_count=len(batches),
        prompt_tokens=prompt_sum if batches and usage_complete else None,
        total_tokens=total_sum if batches and usage_complete else None,
        truncated=source.truncated,
        incomplete_reasons=source.incomplete_reasons,
        chunk_strategy=source.strategy,
        chunk_policy=source.policy,
        chunk_parser=source.parser,
    )
    # 结果包含完整来源与向量；超限拒绝整体，不能返回貌似成功的部分批次。
    encoded = json.dumps(
        asdict(result), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    if len(encoded.encode("utf-8")) > MAX_EMBEDDING_RESULT_BYTES:
        raise EmbeddingError("embedding_result_too_large", "Embedding结果超过输出预算")
    return result
