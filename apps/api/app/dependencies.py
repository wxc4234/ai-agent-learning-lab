"""HTTP 依赖：解析登录身份，并在返回前释放数据库 Session。"""

from typing import Annotated

from fastapi import Depends, Request
from pydantic import SecretStr

from app.config import LOGIN_COOKIE_NAME
from app.database import SessionLocal
from app.services.authentication_service import AuthenticatedUser
from app.services.login_session_resolver import resolve_login_session


def require_current_user(request: Request) -> AuthenticatedUser:
    """读取登录 Cookie，返回不依赖数据库 Session 的安全身份。"""

    raw_token = request.cookies.get(LOGIN_COOKIE_NAME)
    token = SecretStr(raw_token) if raw_token is not None else None

    with SessionLocal() as session:
        identity = resolve_login_session(session, token)

    return identity


CurrentUser = Annotated[
    AuthenticatedUser,
    Depends(require_current_user),
]