import asyncio

from fastapi import APIRouter, Response, status

from app.dependencies import CurrentUser
from app.repositories.runtime.run_repository import (
    load_run_timeline,
    request_run_cancellation,
)
from app.routers.runtime.run_boundary import RunRoute
from app.schemas import CancelRunRequest
from app.services.runtime.run_cancellation import publish_run_cancellation

router = APIRouter(
    tags=["runs"],
    route_class=RunRoute,
)


@router.get("/runs/{run_id}")
def get_run_timeline(
    run_id: int,
    current_user: CurrentUser,
):
    return load_run_timeline(
        run_id=run_id,
        user_id=current_user.id,
    )


@router.post("/runs/{run_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_run(
    run_id: int,
    request: CancelRunRequest,
    current_user: CurrentUser,
) -> Response:
    was_cancelled = await asyncio.to_thread(
        request_run_cancellation,
        run_id=run_id,
        reason=request.reason,
        user_id=current_user.id,
    )
    if was_cancelled:
        await publish_run_cancellation(run_id, request.reason)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
