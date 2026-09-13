"""PostgreSQL-only database fixtures with real commits and isolated teardown."""

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine, make_url

from app.config import settings
from app.database import Base
from app import models  # noqa: F401 -- register every ORM table before create_all


@pytest.fixture(scope="session")
def test_database_url() -> Iterator[URL]:
    """Create only a newly generated database; never reuse or drop an existing one."""
    source = make_url(os.environ.get("TEST_DATABASE_ADMIN_URL", settings.database_url))
    if source.drivername != "postgresql+psycopg":
        pytest.fail("Database tests require postgresql+psycopg; no SQLite fallback")
    database_name = "agent_lab_test_" + uuid4().hex
    admin = create_engine(
        source.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        hide_parameters=True,
    )
    created = False
    try:
        with admin.connect() as connection:
            if connection.scalar(text("SELECT current_database()")) != "postgres":
                pytest.fail("Admin connection must target postgres maintenance database")
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        yield source.set(database=database_name)
    finally:
        try:
            if created:
                # Exact generated name only; no FORCE and no deletion of existing DBs.
                with admin.connect() as connection:
                    connection.execute(text(f'DROP DATABASE "{database_name}"'))
        finally:
            admin.dispose()


@pytest.fixture
def empty_engine(test_database_url: URL) -> Iterator[Engine]:
    """Give each test its own schema, preserving independent connection commits."""
    schema = "test_" + uuid4().hex
    admin = create_engine(test_database_url, hide_parameters=True)
    database = create_engine(
        test_database_url,
        connect_args={"options": f"-csearch_path={schema}"},
        hide_parameters=True,
    )
    created = False
    try:
        with admin.begin() as connection:
            if connection.scalar(text("SELECT current_database()")) != test_database_url.database:
                pytest.fail("Refusing to create schema outside the generated test database")
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        with database.connect() as connection:
            if connection.scalar(text("SELECT current_schema()")) != schema:
                pytest.fail("Test connection must use its generated private schema")
        yield database
    finally:
        database.dispose()
        try:
            if created:
                with admin.begin() as connection:
                    connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        finally:
            admin.dispose()


@pytest.fixture
def engine(empty_engine: Engine) -> Engine:
    """Create current application tables exclusively inside the test schema."""
    Base.metadata.create_all(empty_engine)
    return empty_engine
