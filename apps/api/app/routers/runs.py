import asyncio

from fastapi import APIRouter, HTTPException, Response, status
from redis.exceptions import RedisError

from app.repositories.run_repository import load_run_timeline, request_run_cancellation
from app.schemas import CancelRunRequest
from app.services.run_cancellation import publish_run_cancellation

router = APIRouter(tags=["runs"])


@router.get("/runs/{run_id}")
def get_run_timeline(run_id: int):
    try:
        return load_run_timeline(run_id)
    except ValueError as error:
        raise HTTPException(
            status_code=404,
            detail=f"run_id: {run_id} 不存在",
        ) from error


@router.post("/runs/{run_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_run(run_id: int, request: CancelRunRequest) -> Response:
    try:
        was_cancelled = await asyncio.to_thread(
            request_run_cancellation,
            run_id=run_id,
            reason=request.reason,
        )
        if was_cancelled:
            await publish_run_cancellation(run_id, request.reason)
    except ValueError as error:
        raise HTTPException(
            status_code=404,
            detail=f"run_id: {run_id} 不存在",
        ) from error
    except RedisError as error:
        raise HTTPException(
            status_code=503,
            detail="取消服务暂时不可用",
        ) from error

    return Response(status_code=status.HTTP_204_NO_CONTENT)
