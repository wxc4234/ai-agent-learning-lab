"""构造不缓存的统一错误响应。"""

from fastapi.responses import JSONResponse

from app.schemas import WorkspaceErrorResponse


def _error_response(
    status_code: int,
    *,
    code: str,
    message: str,
) -> JSONResponse:
    """显式构建安全正文，不序列化原始异常或请求输入。"""

    payload = WorkspaceErrorResponse(
        code=code,
        message=message,
    )

    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(),
        headers={"Cache-Control": "no-store"},
    )
