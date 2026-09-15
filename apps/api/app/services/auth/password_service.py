from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """生成包含算法参数和随机盐的密码哈希。"""
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """密码匹配返回 True，不匹配返回 False；其他异常向上传播。"""
    try:
        return _password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
