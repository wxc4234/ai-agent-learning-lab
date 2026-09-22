"""决策服务的真实提交、重新授权、回滚与PostgreSQL锁竞争。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict
from threading import Event
from time import monotonic

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.models import FileEditProposal, Workspace
from app.services.workspace.proposals import file_edit_proposal_decision as decision_service
from app.services.workspace.proposals import file_edit_proposal_service as save_service
from tests.workspace.proposals import test_file_edit_proposal_query as query_tests

root = query_tests.root
target = query_tests.target
database = query_tests.database
setup = query_tests.setup
saved = query_tests.saved


@pytest.fixture
def ready(saved, setup, engine, monkeypatch):
    # 两个服务复用同一隔离数据库，但每次调用仍创建独立Session。
    monkeypatch.setattr(decision_service, "SessionLocal", sessionmaker(
        bind=engine, class_=setup[3], expire_on_commit=False,
    ))
    return saved


def current(engine):
    with Session(engine) as session:
        return session.scalar(select(FileEditProposal.status))


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_commit_public_snapshot_and_unchanged_file(ready, engine, database, decision):
    query, file, sessions = ready
    before = file.read_bytes()
    database[1].clear()
    result = decision_service.decide_task_file_edit_proposal(**query, decision=decision)
    # 独立连接确认提交事实，而非只检查同一个Session中的对象。
    assert current(engine) == result.status == decision
    assert asdict(result) == {**{k: v for k, v in query.items() if k != "user_id"}, "status": decision}
    assert save_service.get_task_file_edit_proposal(**query).status == decision
    assert sessions[-1].closed and file.read_bytes() == before
    with pytest.raises(FrozenInstanceError):
        result.status = "pending"
    locks = [sql.lower() for sql in database[1] if "for update" in sql.lower()]
    assert len(locks) == 4
    assert all(f"from {name}" in sql for name, sql in zip(
        ("workspaces", "tasks", "conversations", "file_edit_proposals"), locks,
    ))


@pytest.mark.parametrize("invalid", [None, True, 1, [], {}, "pending", "APPROVED", ""])
def test_invalid_decision_before_database(ready, invalid):
    query, _, sessions = ready
    before = len(sessions)
    with pytest.raises(decision_service.ProposalDecisionError) as caught:
        decision_service.decide_task_file_edit_proposal(**query, decision=invalid)
    assert caught.value.code == "proposal_decision_invalid"
    assert len(sessions) == before


@pytest.mark.parametrize("first", ["approved", "rejected"])
@pytest.mark.parametrize("second", ["approved", "rejected"])
def test_terminal_decision_cannot_be_overwritten(ready, engine, first, second):
    query, _, _ = ready
    decision_service.decide_task_file_edit_proposal(**query, decision=first)
    with pytest.raises(decision_service.ProposalDecisionError) as caught:
        decision_service.decide_task_file_edit_proposal(**query, decision=second)
    assert caught.value.code == "proposal_state_conflict"
    assert current(engine) == first


@pytest.mark.parametrize("kind", [
    "foreign-user", "missing-workspace", "missing-task", "missing-proposal",
    "sibling-task", "wrong-workspace", "foreign-conversation", "missing-conversation",
    "changed-owner", "deleted-task",
])
def test_decision_reauthorizes_every_resource(ready, engine, target, kind, monkeypatch):
    # 复用查询边界的真实数据变更场景，实际被调用的是本课决策服务。
    def decide(**query):
        return decision_service.decide_task_file_edit_proposal(**query, decision="approved")

    monkeypatch.setattr(query_tests.service, "get_task_file_edit_proposal", decide)
    query_tests.test_all_inaccessible_resources_use_same_safe_error(ready, engine, target, kind)
    assert current(engine) in (None, "pending")


@pytest.mark.parametrize("binding", [None, "/changed"])
@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_binding_change_prevents_only_approval(ready, engine, binding, decision):
    query, file, _ = ready
    before = file.read_bytes()
    with Session(engine) as session, session.begin():
        session.scalar(select(Workspace)).root_path = binding
    if decision == "approved":
        with pytest.raises(save_service.ProposalBindingChangedError):
            decision_service.decide_task_file_edit_proposal(**query, decision=decision)
        assert current(engine) == "pending"
    else:
        decision_service.decide_task_file_edit_proposal(**query, decision=decision)
        assert current(engine) == "rejected"
    assert file.read_bytes() == before


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_truncated_diff_prevents_only_approval(ready, engine, decision):
    query, _, _ = ready
    with Session(engine) as session, session.begin():
        session.scalar(select(FileEditProposal)).diff_truncated = True
    if decision == "approved":
        with pytest.raises(decision_service.ProposalDecisionError) as caught:
            decision_service.decide_task_file_edit_proposal(**query, decision=decision)
        assert caught.value.code == "proposal_diff_incomplete"
        assert current(engine) == "pending"
    else:
        decision_service.decide_task_file_edit_proposal(**query, decision=decision)
        assert current(engine) == "rejected"


@pytest.mark.parametrize("change", ["modified", "deleted"])
def test_approval_is_not_a_file_baseline_check(ready, change):
    query, file, _ = ready
    if change == "deleted":
        file.unlink()
    else:
        file.write_bytes(b"external editor")
    assert decision_service.decide_task_file_edit_proposal(**query, decision="approved").status == "approved"
    if change == "deleted":
        assert not file.exists()
    else:
        assert file.read_bytes() == b"external editor"


@pytest.mark.parametrize("phase", ["after_flush", "before_commit"])
def test_failure_rolls_back_and_closes_session(ready, setup, engine, phase):
    query, file, sessions = ready
    before = file.read_bytes()

    def fail(*args):
        raise RuntimeError("controlled transaction failure")

    event.listen(setup[3], phase, fail)
    try:
        with pytest.raises(RuntimeError, match="controlled"):
            decision_service.decide_task_file_edit_proposal(**query, decision="approved")
    finally:
        event.remove(setup[3], phase, fail)
    assert current(engine) == "pending"
    assert sessions[-1].closed and not sessions[-1].in_transaction()
    assert file.read_bytes() == before


@pytest.mark.parametrize("first,second", [("approved", "approved"), ("approved", "rejected"), ("rejected", "approved")])
@pytest.mark.parametrize("rollback", [False, True])
def test_real_competing_decisions_wait_and_recheck(ready, engine, monkeypatch, first, second, rollback):
    query, _, _ = ready
    original = decision_service.lock_file_edit_proposal_for_decision
    held = Event()
    release = Event()
    blocker = []

    def hold(*args, **kwargs):
        row = original(*args, **kwargs)
        if not held.is_set():
            blocker.append(args[0].scalar(text("SELECT pg_backend_pid()")))
            held.set()
            if not release.wait(8):
                raise RuntimeError("test lock release timed out")
            if rollback:
                raise RuntimeError("controlled rollback")
        return row

    monkeypatch.setattr(decision_service, "lock_file_edit_proposal_for_decision", hold)
    with ThreadPoolExecutor(max_workers=2) as pool:
        leader = pool.submit(decision_service.decide_task_file_edit_proposal, **query, decision=first)
        try:
            assert held.wait(5)
            follower = pool.submit(decision_service.decide_task_file_edit_proposal, **query, decision=second)
            deadline = monotonic() + 5
            blocked = False
            # 根据数据库锁等待事实同步，不以固定sleep猜测第二个请求已到达。
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
                while monotonic() < deadline:
                    blocked = observer.scalar(text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE :pid = ANY(pg_blocking_pids(pid)))"
                    ), {"pid": blocker[0]})
                    if blocked:
                        break
            assert blocked and not follower.done()
        finally:
            release.set()
        if rollback:
            with pytest.raises(RuntimeError, match="controlled rollback"):
                leader.result(timeout=5)
            assert follower.result(timeout=5).status == second
        else:
            assert leader.result(timeout=5).status == first
            with pytest.raises(decision_service.ProposalDecisionError) as caught:
                follower.result(timeout=5)
            assert caught.value.code == "proposal_state_conflict"
    assert current(engine) == (second if rollback else first)


@pytest.mark.parametrize("sql", [
    "UPDATE workspaces SET root_path='/changed'",
    "UPDATE conversations SET user_id=:other",
    "DELETE FROM tasks WHERE id=:task",
])
def test_decision_holds_ownership_and_deletion_locks(ready, engine, target, monkeypatch, sql):
    query, _, _ = ready
    original = decision_service.lock_file_edit_proposal_for_decision

    def check(*args, **kwargs):
        row = original(*args, **kwargs)
        with pytest.raises(DBAPIError) as caught, engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '100ms'"))
            connection.execute(text(sql), {"other": target["other_id"], "task": target["task_pk"]})
        assert caught.value.orig.sqlstate == "55P03"
        return row

    monkeypatch.setattr(decision_service, "lock_file_edit_proposal_for_decision", check)
    decision_service.decide_task_file_edit_proposal(**query, decision="approved")
    assert current(engine) == "approved"
