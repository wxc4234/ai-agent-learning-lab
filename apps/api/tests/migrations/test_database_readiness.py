"""启动只读检查与真实 Alembic 迁移，始终使用根隔离 PostgreSQL 夹具。"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect, text
from sqlalchemy.exc import OperationalError

from app import database
from app.main import app
from app import main


def migrate(engine, revision='head'):
    config = Config(str(Path(__file__).resolve().parents[2] / 'alembic.ini'))
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, revision)


@pytest.mark.parametrize('revision', [None, 'e05f42c817ab', 'head', 'unknown'])
def test_version_boundary_and_no_writes(empty_engine, monkeypatch, revision):
    monkeypatch.setattr(database, 'engine', empty_engine)
    if revision:
        migrate(empty_engine, 'head' if revision == 'unknown' else revision)
    if revision == 'unknown':
        with empty_engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num='future_revision'"))
    before = set(inspect(empty_engine).get_table_names())
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().lower())

    event.listen(empty_engine, 'before_cursor_execute', capture)
    try:
        if revision == 'head':
            database.check_database_ready()
        else:
            with pytest.raises(database.DatabaseNotReadyError, match='迁移版本'):
                database.check_database_ready()
    finally:
        event.remove(empty_engine, 'before_cursor_execute', capture)
    assert statements and all(sql.startswith('select') for sql in statements)
    assert set(inspect(empty_engine).get_table_names()) == before
    assert empty_engine.pool.checkedout() == 0
    if revision is None:
        assert before == set()


def test_connect_failure_is_safe(monkeypatch):
    class BrokenEngine:
        def connect(self):
            raise OperationalError('PRIVATE SQL', {}, Exception('PRIVATE password'))
    monkeypatch.setattr(database, 'engine', BrokenEngine())
    with pytest.raises(database.DatabaseNotReadyError) as caught:
        database.check_database_ready()
    assert 'PRIVATE' not in str(caught.value)
    assert caught.value.__suppress_context__


def test_lifespan_after_real_migration(empty_engine, monkeypatch):
    migrate(empty_engine)
    monkeypatch.setattr(database, 'engine', empty_engine)
    closed = []

    async def close():
        closed.append(True)

    monkeypatch.setattr(main, 'close_cancellation_broker', close)
    # 不发送业务请求，仅确认真实 lifespan 检查通过并执行资源清理。
    with TestClient(app):
        assert closed == []
    assert closed == [True]
    assert empty_engine.pool.checkedout() == 0


def test_empty_database_refuses_lifespan(empty_engine, monkeypatch):
    monkeypatch.setattr(database, 'engine', empty_engine)
    with pytest.raises(database.DatabaseNotReadyError), TestClient(app):
        pytest.fail('unmigrated database must not start')
    assert inspect(empty_engine).get_table_names() == []
