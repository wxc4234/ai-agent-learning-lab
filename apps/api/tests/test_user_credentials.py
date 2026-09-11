import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

from app.models import User
from app.services.password_service import hash_password, verify_password


@pytest.fixture
def connection():
    engine = sa.create_engine("sqlite://")
    User.__table__.create(engine)
    with engine.begin() as conn:
        yield conn
    engine.dispose()


def test_multiple_legacy_users_remain_valid(connection):
    connection.execute(
        User.__table__.insert(),
        [
            {"external_id": "legacy-1"},
            {"external_id": "legacy-2"},
        ],
    )
    rows = connection.execute(sa.select(User.username, User.password_hash)).all()
    assert rows == [(None, None), (None, None)]


def test_complete_credentials_round_trip(connection):
    encoded = hash_password("Learning-Agent-2026!")
    connection.execute(
        User.__table__.insert().values(
            external_id="registered",
            username="learner",
            password_hash=encoded,
        )
    )
    stored = connection.scalar(sa.select(User.password_hash))
    assert stored == encoded
    assert verify_password("Learning-Agent-2026!", stored)


@pytest.mark.parametrize("username,password_hash", [("learner", None), (None, "hash")])
def test_partial_credentials_rejected(connection, username, password_hash):
    with pytest.raises(IntegrityError, match="ck_users_login_credentials_pair"):
        connection.execute(
            User.__table__.insert().values(
                external_id="partial",
                username=username,
                password_hash=password_hash,
            )
        )


def test_duplicate_username_rejected(connection):
    connection.execute(
        User.__table__.insert().values(
            external_id="first",
            username="learner",
            password_hash="test-hash",
        )
    )
    with pytest.raises(IntegrityError):
        connection.execute(
            User.__table__.insert().values(
                external_id="second",
                username="learner",
                password_hash="test-hash",
            )
        )


def test_update_cannot_clear_only_password(connection):
    connection.execute(
        User.__table__.insert().values(
            external_id="first",
            username="learner",
            password_hash="test-hash",
        )
    )
    with pytest.raises(IntegrityError, match="ck_users_login_credentials_pair"):
        connection.execute(User.__table__.update().values(password_hash=None))


def test_migration_preserves_legacy_data_and_can_round_trip():
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations/versions/a91c42e7d603_add_user_login_credentials.py"
    )
    spec = importlib.util.spec_from_file_location("credentials_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, external_id VARCHAR(100) "
                "NOT NULL UNIQUE, create_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL)"
            )
        )
        conn.execute(
            sa.text("INSERT INTO users (id, external_id) VALUES (1, 'legacy')")
        )
        before = conn.execute(sa.text("SELECT * FROM users")).all()
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            assert (
                conn.execute(
                    sa.text("SELECT id, external_id, create_at FROM users")
                ).all()
                == before
            )
            assert conn.execute(
                sa.text("SELECT username, password_hash FROM users")
            ).one() == (None, None)
            with pytest.raises(IntegrityError):
                conn.execute(sa.text("UPDATE users SET username = 'incomplete'"))
            migration.downgrade()
            assert conn.execute(sa.text("SELECT * FROM users")).all() == before
            assert "username" not in {
                col["name"] for col in sa.inspect(conn).get_columns("users")
            }
            migration.upgrade()
            assert conn.execute(
                sa.text("SELECT username, password_hash FROM users")
            ).one() == (None, None)
    engine.dispose()
