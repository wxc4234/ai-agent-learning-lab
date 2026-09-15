import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

from app.models import User
from app.services.auth.password_service import hash_password, verify_password

def _get_users_table() -> sa.Table:
    table = User.__table__
    assert isinstance(table, sa.Table)
    return table


users_table = _get_users_table()


@pytest.fixture
def connection(engine):
    with engine.connect() as conn:
        yield conn


def test_multiple_legacy_users_remain_valid(connection):
    connection.execute(
        users_table.insert(),
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
        users_table.insert().values(
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
            users_table.insert().values(
                external_id="partial",
                username=username,
                password_hash=password_hash,
            )
        )


def test_duplicate_username_rejected(connection):
    connection.execute(
        users_table.insert().values(
            external_id="first",
            username="learner",
            password_hash="test-hash",
        )
    )
    with pytest.raises(IntegrityError):
        connection.execute(
            users_table.insert().values(
                external_id="second",
                username="learner",
                password_hash="test-hash",
            )
        )


def test_update_cannot_clear_only_password(connection):
    connection.execute(
        users_table.insert().values(
            external_id="first",
            username="learner",
            password_hash="test-hash",
        )
    )
    with pytest.raises(IntegrityError, match="ck_users_login_credentials_pair"):
        connection.execute(users_table.update().values(password_hash=None))


def test_migration_preserves_legacy_data_and_can_round_trip(empty_engine):
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations/versions/a91c42e7d603_add_user_login_credentials.py"
    )
    spec = importlib.util.spec_from_file_location("credentials_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with empty_engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, external_id VARCHAR(100) "
                "NOT NULL UNIQUE, create_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL)"
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
            with pytest.raises(IntegrityError), conn.begin_nested():
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
