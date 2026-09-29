"""授权快照与真实沙箱：项目副本修改不能回写源项目。"""
import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Conversation
from app.services.runtime.sandbox import project_snapshot as snapshot
from app.services.runtime.sandbox.project_command import run_project_command
from app.services.runtime.sandbox.task_sample_command_journal import TaskSampleCommandJournal
from app.services.runtime.command.command_contracts import CommandRequest
from app.tools.context import ToolExecutionContext
from tests.workspace.proposals.test_project_write_execution import (
    grants, ready, setup, saved, root, target, database, execution_database,
)
__all__ = ['database', 'execution_database', 'grants', 'ready', 'root', 'saved', 'setup', 'target']

@pytest.fixture
def context(ready, engine, setup, monkeypatch):
    factory = sessionmaker(bind=engine, class_=setup[3])
    monkeypatch.setattr(snapshot, 'SessionLocal', factory)
    from app.services.runtime.agent import tool_execution_context
    monkeypatch.setattr(tool_execution_context, 'SessionLocal', factory)
    with Session(engine) as session:
        conversation = session.scalars(select(Conversation)).first()
        assert conversation is not None
        scope = ready[0]
        return ToolExecutionContext(user_id=scope['user_id'], workspace_id=scope['workspace_id'],
                                    task_id=scope['task_id'], conversation_id=conversation.external_id)


def test_snapshot_exact_bytes_and_exclusions(context, ready, root):
    file = ready[1]
    (file.parent / '.env').write_text('secret')
    result = snapshot.read_project_snapshot(context)
    assert result[file.relative_to(root).as_posix()] == file.read_bytes()
    assert '.env' not in result
    assert snapshot.snapshot_digest(result) == snapshot.snapshot_digest(dict(reversed(list(result.items()))))


@pytest.mark.parametrize('kind', ['symlink', 'oversize', 'fifo'])
def test_snapshot_rejects_incomplete_sources(context, ready, kind):
    path = ready[1].parent / 'bad'
    if kind == 'symlink':
        path.symlink_to(ready[1])
    elif kind == 'fifo':
        import os
        os.mkfifo(path)
    else:
        path.write_bytes(b'x' * (snapshot.MAX_BYTES + 1))
    with pytest.raises(ValueError):
        snapshot.read_project_snapshot(context)


def test_project_command_real_container(context, ready, root):
    file = ready[1]
    original = file.read_bytes()
    command = CommandRequest(argv=['/usr/local/bin/python', '-c',
        f"from pathlib import Path; p=Path({file.relative_to(root).as_posix()!r}); print(p.read_text()); p.write_text('container only')"])
    journal = TaskSampleCommandJournal(user_id=context.user_id, conversation_id=context.conversation_id)
    result = asyncio.run(run_project_command(request=command, context=context, journal=journal))
    assert result.command.exit_code == 0
    assert result.sample_cleaned
    assert file.read_bytes() == original
    assert journal.records[0].status == 'completed'


@pytest.mark.parametrize('argv,expected', [
    (['/usr/local/bin/python', '-c', 'raise SystemExit(7)'], 7),
    (['/usr/local/bin/python', '-c', "import os; assert 'LOCAL_RUNTIME_TOKEN' not in os.environ; assert not os.path.exists('/Users')"], 0),
])
def test_custom_verification_and_host_boundary(context, argv, expected):
    journal = TaskSampleCommandJournal(user_id=context.user_id, conversation_id=context.conversation_id)
    result = asyncio.run(run_project_command(request=CommandRequest(argv=argv), context=context, journal=journal))
    assert result.command.exit_code == expected
    assert result.sample_cleaned


def test_large_project_uses_dedicated_carrier_budget(context, root):
    # 超过旧160KiB/256文件预算；不改动原单文件样例工厂的256KiB契约。
    for index in range(300):
        (root / f'module_{index}.py').write_text('#' + 'x' * 4090 + '\n')
    journal = TaskSampleCommandJournal(user_id=context.user_id, conversation_id=context.conversation_id)
    result = asyncio.run(run_project_command(request=CommandRequest(argv=['/usr/local/bin/python', '-c',
        "import pathlib; files=list(pathlib.Path('.').glob('module_*.py')); assert len(files)==300; [compile(p.read_text(),str(p),'exec') for p in files]"]),
        context=context, journal=journal))
    assert result.command.exit_code == 0 and result.sample_cleaned
