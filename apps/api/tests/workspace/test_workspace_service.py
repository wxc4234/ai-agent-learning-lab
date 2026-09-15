"""Workspace 服务的事务边界：使用独立 PostgreSQL，允许真实提交和回滚。"""

from dataclasses import FrozenInstanceError, asdict
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.models import User, Workspace
from app.repositories.workspace.workspace_repository import InvalidWorkspaceNameError
from app.services.workspace import workspace_service as service


@pytest.fixture
def owner_id(engine):
    # 提前提交身份，保证被测服务收到的 Session 没有准备数据时留下的事务。
    with Session(engine) as session, session.begin():
        owner = User(external_id="workspace-service-owner")
        session.add(owner)
        session.flush()
        return owner.id


def workspace_count(engine):
    # 使用独立连接观察数据库事实，避免 Session 身份映射造成假阳性。
    with Session(engine) as reader:
        return reader.scalar(select(func.count()).select_from(Workspace))


@pytest.mark.parametrize("expire_on_commit", [True, False])
def test_success_returns_detached_safe_result_without_reopening_transaction(engine, owner_id, expire_on_commit):
    with Session(engine, expire_on_commit=expire_on_commit) as session:
        result = service.create_user_workspace(session, user_id=owner_id, name=" \t我的 Agent 项目\n")
        # 若提交后又访问过期 ORM 属性，这里会出现新的自动事务。
        assert not session.in_transaction()
        assert session.is_active
        with Session(engine) as reader:
            stored = reader.scalar(select(Workspace))
            assert stored.user_id == owner_id
            assert stored.external_id == result.external_id
            assert stored.name == result.name == "我的 Agent 项目"
            assert stored.created_at == result.created_at
    # Session 关闭后结果仍可读取，且只包含允许公开的普通字段。
    assert set(asdict(result)) == {"external_id", "name", "created_at"}
    assert UUID(hex=result.external_id).version == 4
    assert result.created_at.tzinfo is not None
    with pytest.raises(FrozenInstanceError):
        result.name = "不能修改"


@pytest.mark.parametrize("name", ["", " \t\n\u3000", "a" * 101, "中" * 101])
def test_invalid_name_preserves_classification_and_session_reusability(engine, owner_id, name):
    with Session(engine) as session:
        with pytest.raises(InvalidWorkspaceNameError):
            service.create_user_workspace(session, user_id=owner_id, name=name)
        assert not session.in_transaction()
        assert session.is_active
        assert workspace_count(engine) == 0
        service.create_user_workspace(session, user_id=owner_id, name="恢复创建")
    assert workspace_count(engine) == 1


@pytest.mark.parametrize("transaction_kind", ["explicit", "read", "pending", "flushed"])
@pytest.mark.parametrize("name", ["正常名称", " "])
def test_existing_transaction_is_rejected_without_touching_caller_work(engine, owner_id, transaction_kind, name):
    with Session(engine) as session:
        pending = None
        if transaction_kind == "explicit":
            session.begin()
        elif transaction_kind == "read":
            session.execute(select(User.id))
        else:
            pending = User(external_id="caller-pending")
            session.add(pending)
            if transaction_kind == "flushed":
                session.flush()
        transaction = session.get_transaction()
        with pytest.raises(RuntimeError, match="无活动事务"):
            service.create_user_workspace(session, user_id=owner_id, name=name)
        # 即使名称非法，也应先拒绝已有事务；服务不得提交或回滚调用方工作。
        assert session.get_transaction() is transaction
        assert transaction.is_active
        if transaction_kind == "pending":
            assert pending in session.new and pending.id is None
        assert workspace_count(engine) == 0
        session.commit()
    if pending is not None:
        with Session(engine) as reader:
            assert reader.scalar(select(User).where(User.external_id == "caller-pending")) is not None


@pytest.mark.parametrize("error_type", [IntegrityError, OperationalError])
def test_commit_failure_rolls_back_flushed_row_and_preserves_exception(engine, owner_id, monkeypatch, error_type):
    failure = error_type("COMMIT", None, RuntimeError("synthetic commit failure"))
    with Session(engine) as session:
        with monkeypatch.context() as patch:
            def fail_commit():
                # 确認故障发生在真实 INSERT/flush 之后，而非提前跳过仓储。
                assert session.scalar(select(func.count()).select_from(Workspace)) == 1
                raise failure

            patch.setattr(session, "commit", fail_commit)
            with pytest.raises(error_type) as caught:
                service.create_user_workspace(session, user_id=owner_id, name="未提交")
            assert caught.value is failure
        assert not session.in_transaction()
        assert session.is_active
        assert workspace_count(engine) == 0
        service.create_user_workspace(session, user_id=owner_id, name="恢复")
    assert workspace_count(engine) == 1


def test_real_sql_failure_after_insert_rolls_back_and_session_recovers(engine, owner_id, monkeypatch):
    original = service.create_workspace

    def insert_then_fail(session, **kwargs):
        original(session, **kwargs)
        # 真实 PostgreSQL 错误使事务失效，服务必须 rollback 才能复用连接。
        session.execute(text("SELECT * FROM missing_workspace_service_table"))

    with Session(engine) as session:
        with monkeypatch.context() as patch:
            patch.setattr(service, "create_workspace", insert_then_fail)
            with pytest.raises(DBAPIError):
                service.create_user_workspace(session, user_id=owner_id, name="失败")
        assert not session.in_transaction()
        assert session.is_active
        assert workspace_count(engine) == 0
        service.create_user_workspace(session, user_id=owner_id, name="恢复")


def test_result_construction_failure_prevents_commit(engine, owner_id, monkeypatch):
    failure = RuntimeError("result construction failed")

    def fail_result(**kwargs):
        raise failure

    with Session(engine) as session:
        with monkeypatch.context() as patch:
            patch.setattr(service, "WorkspaceCreationResult", fail_result)
            with pytest.raises(RuntimeError) as caught:
                service.create_user_workspace(session, user_id=owner_id, name="不能返回")
            assert caught.value is failure
        assert not session.in_transaction()
        assert workspace_count(engine) == 0


def test_unknown_user_keeps_foreign_key_error(engine, owner_id):
    with Session(engine) as session:
        with pytest.raises(IntegrityError) as caught:
            service.create_user_workspace(session, user_id=owner_id + 100, name="无效身份")
        assert caught.value.orig.sqlstate == "23503"
        assert not session.in_transaction()
        assert session.is_active
        assert workspace_count(engine) == 0
        service.create_user_workspace(session, user_id=owner_id, name="有效身份")


def test_repeated_name_is_not_treated_as_duplicate_request(engine, owner_id):
    with Session(engine) as session:
        first = service.create_user_workspace(session, user_id=owner_id, name="同名")
        second = service.create_user_workspace(session, user_id=owner_id, name="同名")
        assert first.external_id != second.external_id
    assert workspace_count(engine) == 2
