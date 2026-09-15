import pytest
from argon2.exceptions import InvalidHashError, VerificationError

from app.services.auth import password_service


@pytest.fixture(scope="module")
def password_hash():
    return password_service.hash_password("Learning-Agent-2026!")


def test_hash_uses_argon2id(password_hash):
    assert password_hash.startswith("$argon2id$")
    assert "Learning-Agent-2026!" not in password_hash


def test_correct_password_matches(password_hash):
    assert password_service.verify_password("Learning-Agent-2026!", password_hash)


def test_wrong_password_returns_false(password_hash):
    assert password_service.verify_password("wrong-password", password_hash) is False


def test_random_salts_produce_different_valid_hashes(password_hash):
    another_hash = password_service.hash_password("Learning-Agent-2026!")
    assert another_hash != password_hash
    assert password_service.verify_password("Learning-Agent-2026!", another_hash)


def test_whitespace_and_unicode_are_preserved():
    password = "  学习-Agent-2026!  "
    encoded = password_service.hash_password(password)
    assert password_service.verify_password(password, encoded)
    assert password_service.verify_password(password.strip(), encoded) is False


def test_invalid_hash_is_not_a_password_mismatch():
    with pytest.raises(InvalidHashError):
        password_service.verify_password("password", "not-an-argon2-hash")


def test_verification_failure_propagates(monkeypatch, password_hash):
    class BrokenHasher:
        def verify(self, encoded, password):
            raise VerificationError("verification unavailable")

    monkeypatch.setattr(password_service, "_password_hasher", BrokenHasher())
    with pytest.raises(VerificationError, match="verification unavailable"):
        password_service.verify_password("Learning-Agent-2026!", password_hash)
