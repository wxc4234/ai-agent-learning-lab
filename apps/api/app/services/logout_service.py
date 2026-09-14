"""登出服务：撤销当前登录会话，由服务统一管理事务。"""

import re
from datetime import UTC, datetime
from hashlib import sha256

from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.repositories.login_session_repository import revoke_login_session


def logout_user(
    session: Session,
    token: SecretStr | None,
    *,
    now: datetime | None = None,
) -> bool:
    """返回本次是否实际撤销记录；调用方负责关闭 Session。"""

    if session.in_transaction():
        raise RuntimeError("登出服务需要无活动事务的 Session")

    if token is None:
        return False

    raw_token = token.get_secret_value()

    if re.fullmatch(r"[A-Za-z0-9_-]{43}", raw_token) is None:
        return False

    try:
        token_hash = sha256(raw_token.encode("utf-8")).hexdigest()
        revoked_at = datetime.now(UTC) if now is None else now

        revoked = revoke_login_session(
            session,
            token_hash=token_hash,
            now=revoked_at,
        )

        session.commit()
    except Exception:
        session.rollback()
        raise

    return revoked