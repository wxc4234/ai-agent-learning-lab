"""登录会话存储：创建、查询有效记录和撤销，由调用方管理事务。"""

import re
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import LoginSession


def _as_utc(value: datetime) -> datetime:
    """拒绝无时区时间，避免隐式解释成本机时区。"""
    if value.utcoffset() is None:
        raise ValueError("登录会话时间必须包含时区")

    return value.astimezone(UTC)


def _validate_token_hash(token_hash: str) -> None:
    """只接受 SHA-256 十六进制摘要，不在错误中回显输入。"""

    if re.fullmatch(r"[0-9a-f]{64}", token_hash) is None:
        raise ValueError("会话令牌摘要格式不正确")


def create_login_session(
    session: Session,
    *,
    user_id: int,
    token_hash: str,
    created_at: datetime,
    expires_at: datetime,
) -> LoginSession:
    """创建会话并 flush，不提交事务。"""

    _validate_token_hash(token_hash)
    created_at = _as_utc(created_at)
    expires_at = _as_utc(expires_at)

    if expires_at <= created_at:
        raise ValueError("过期时间必须晚于创建时间")

    login_session = LoginSession(
        user_id=user_id,
        token_hash=token_hash,
        created_at=created_at,
        expires_at=expires_at,
    )

    session.add(login_session)
    session.flush()

    return login_session


def get_active_login_session(
    session: Session, *, token_hash: str, now: datetime
) -> LoginSession | None:
    """查询当前有效的会话；恰好到达过期时间也视为失效。"""

    _validate_token_hash(token_hash)
    now = _as_utc(now)

    return session.scalar(
        select(LoginSession).where(
            LoginSession.token_hash == token_hash,
            LoginSession.created_at <= now,
            LoginSession.expires_at > now,
            LoginSession.revoked_at.is_(None),
        )
    )


def revoke_login_session(session: Session, *, token_hash: str, now: datetime) -> bool:
    """撤销已有会话；不存在或已撤销返回 False，不提交事务。"""

    _validate_token_hash(token_hash)
    now = _as_utc(now)

    revoked_id = session.scalar(
        update(LoginSession)
        .where(
            LoginSession.token_hash == token_hash,
            LoginSession.created_at <= now,
            LoginSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
        .returning(LoginSession.id)
    )

    return revoked_id is not None
