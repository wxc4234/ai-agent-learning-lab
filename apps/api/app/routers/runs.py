from fastapi import APIRouter, HTTPException

from app.repositories.run_repository import load_run_timeline

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
