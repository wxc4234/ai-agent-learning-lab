"""使用 Redis 在任意 API 实例间协调流式 Run 的取消。"""

from typing import Literal

from redis import asyncio as redis_asyncio
from redis.asyncio.client import Redis

from app.config import settings

CancellationReason = Literal["user", "timeout"]

_CANCELLATION_TTL_SECONDS = 300
_redis_client: Redis | None = None


def _cancellation_key(run_id: int) -> str:
    return f"agent-run:{run_id}:cancellation"


def _cancellation_channel(run_id: int) -> str:
    return f"agent-run:{run_id}:cancellation-events"


def _get_redis_client() -> Redis:
    global _redis_client

    if _redis_client is None:
        _redis_client = redis_asyncio.from_url(
            settings.redis_url,
            decode_responses=True,
        )

    return _redis_client


def _parse_reason(value: bytes | str | None) -> CancellationReason | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8")

    if value in ("user", "timeout"):
        return value

    return None


async def publish_run_cancellation(
    run_id: int,
    reason: CancellationReason,
) -> None:
    """持久化短期取消信号，并通知承载该流的 API 实例。"""

    client = _get_redis_client()
    await client.set(
        _cancellation_key(run_id),
        reason,
        ex=_CANCELLATION_TTL_SECONDS,
    )
    await client.publish(_cancellation_channel(run_id), reason)


async def wait_for_run_cancellation(run_id: int) -> CancellationReason:
    """等待 Redis 中出现取消信号，兼顾发布订阅前后的竞态。"""

    client = _get_redis_client()
    existing_reason = _parse_reason(await client.get(_cancellation_key(run_id)))
    if existing_reason is not None:
        return existing_reason

    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(_cancellation_channel(run_id))

        # 订阅建立前可能刚好收到取消请求，因此重新读取一次 key。
        existing_reason = _parse_reason(await client.get(_cancellation_key(run_id)))
        if existing_reason is not None:
            return existing_reason

        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=None,
            )
            if message is None:
                continue

            reason = _parse_reason(message["data"])
            if reason is not None:
                return reason
    finally:
        await pubsub.unsubscribe(_cancellation_channel(run_id))
        await pubsub.aclose()


async def close_cancellation_broker() -> None:
    """在 FastAPI 关闭时释放 Redis 客户端连接。"""

    global _redis_client

    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
