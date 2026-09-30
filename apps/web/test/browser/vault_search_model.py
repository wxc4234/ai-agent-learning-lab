"""隔离 PC 验收：模型决策受控，Vault 读取、工具和聊天持久化使用真实实现。"""

from contextlib import asynccontextmanager
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import (
    AgentRun,
    AgentRunEvent,
    Conversation,
    ConversationExecutionSlot,
    Message,
    Task,
    Workspace,
)
from app.services.auth.local_identity import resolve_local_identity
from app.services.runtime.agent.agent_runtime import (
    FinalAnswer,
    ModelUsage,
    ToolAction,
    ToolErrorObservation,
)
from app.tools import vault_search as adapter


OUTPUT = Path(__file__).resolve().parents[2] / "output/playwright/vault-search"
MODES = ("match", "empty", "file_budget", "match_budget", "inventory_budget", "snippet", "error")


def vault_search_decision(prompt, observations):
    usage = ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20)
    mode = next(mode for mode in MODES if f"[{mode}]" in prompt)
    if observations:
        observation = observations[-1]
        if mode == "error":
            assert isinstance(observation, ToolErrorObservation)
            assert observation.details == "vault_search_unavailable"
        else:
            result = json.loads(observation.result)
            assert result["source"] == "authorized_vault"
            assert result["content_trust"] == "untrusted"
        return FinalAnswer(content=f"Vault 浏览器验收 {mode} 已完成。", model_usage=usage)
    return ToolAction(
        tool_call_id=f"vault-browser-{mode}",
        tool_name="search_vault",
        arguments=json.dumps({"query": "不存在的词" if mode in ("empty", "file_budget") else "Docker"}),
        model_usage=usage,
    )


def snapshot(root):
    # 同时核对内容、目录成员、inode 和修改时间，禁止只靠 UI 推断只读。
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        for path in root.rglob("*") if path.is_file() and not path.is_symlink()
    }


def install_vault_search_fixture(app):
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        OUTPUT.mkdir(parents=True, exist_ok=True)
        for name in ("evidence.json", "server-evidence.json", "cleanup-evidence.json", "database-evidence.json"):
            (OUTPUT / name).unlink(missing_ok=True)
        fixtures, calls, bindings = [], [], {}
        with TemporaryDirectory(prefix="vault-browser-") as directory:
            parent = Path(directory).resolve()
            sentinel = parent / "outside.md"
            sentinel.write_text("Docker 不应读取的外部资料\n", encoding="utf-8")
            async with original(application):
                with SessionLocal() as session:
                    user = resolve_local_identity(session)
                for mode in MODES:
                    root = parent / mode
                    root.mkdir()
                    (root / ".obsidian").mkdir()
                    (root / ".obsidian/private.md").write_text("Docker 隐藏配置\n", encoding="utf-8")
                    (root / "ordinary.txt").write_text("Docker 非 Markdown\n", encoding="utf-8")
                    (root / "linked.md").symlink_to(sentinel)
                    if mode == "file_budget":
                        for index in range(21):
                            (root / f"{index:02d}.md").write_text("没有目标\n", encoding="utf-8")
                    elif mode == "inventory_budget":
                        (root / "note.md").write_text("Docker 清单部分命中\n", encoding="utf-8")
                        for index in range(21):
                            (root / f"directory-{index:02d}").mkdir()
                    elif mode == "match_budget":
                        (root / "note.md").write_text("Docker 匹配\n" * 51, encoding="utf-8")
                    elif mode == "snippet":
                        (root / "note.md").write_text("a" * 300 + "Docker" + "b" * 300 + "\n", encoding="utf-8")
                    elif mode == "error":
                        (root / "note.md").write_bytes(b"\xff")
                    else:
                        (root / "notes").mkdir()
                        (root / "notes/中文笔记.md").write_text(
                            '背景资料\n😀Docker <img src=x onerror="window.vaultInjected=true">\n',
                            encoding="utf-8",
                        )
                    workspace_id, task_id, conversation_id = (uuid4().hex for _ in range(3))
                    # 数据库事务只提交真实归属；文件夹准备和扫描不跨越事务。
                    with SessionLocal() as session, session.begin():
                        workspace = Workspace(external_id=workspace_id, name=f"Vault-{mode}", user_id=user.id, root_path=str(root))
                        task = Task(external_id=task_id, title=f"Vault-{mode}", workspace=workspace)
                        session.add_all([workspace, task, Conversation(external_id=conversation_id, user_id=user.id, task=task)])
                    bindings[workspace_id] = str(root)
                    fixtures.append({"mode": mode, "workspace_id": workspace_id, "task_id": task_id, "conversation_id": conversation_id})
                before = snapshot(parent)
                original_search = adapter.search_vault_markdown

                def tracked_search(**kwargs):
                    assert kwargs["user_id"] == user.id
                    assert any(item["workspace_id"] == kwargs["workspace_id"] and item["task_id"] == kwargs["task_id"] for item in fixtures)
                    calls.append(kwargs)
                    return original_search(**kwargs)

                adapter.search_vault_markdown = tracked_search
                (OUTPUT / "fixtures.json").write_text(json.dumps(fixtures), encoding="utf-8")
                try:
                    yield
                finally:
                    adapter.search_vault_markdown = original_search
                    assert snapshot(parent) == before
                    assert len(calls) == len(MODES)
                    assert len({item["task_id"] for item in calls}) == len(MODES)
                    with SessionLocal() as session:
                        rows = session.scalars(select(Workspace).where(Workspace.external_id.in_(bindings))).all()
                        assert {row.external_id: row.root_path for row in rows} == bindings
                    (OUTPUT / "server-evidence.json").write_text(json.dumps({
                        "files_unchanged": True, "bindings_unchanged": True, "read_calls": len(calls),
                    }), encoding="utf-8")
        assert not parent.exists()
        (OUTPUT / "cleanup-evidence.json").write_text(json.dumps({"vault_directories_removed": len(MODES)}), encoding="utf-8")

    app.router.lifespan_context = lifespan


def verify_vault_rows(engine):
    report = json.loads((OUTPUT / "evidence.json").read_text(encoding="utf-8"))
    fixtures = json.loads((OUTPUT / "fixtures.json").read_text(encoding="utf-8"))
    assert len(report) == len(fixtures) == len(MODES)
    with Session(engine) as session:
        assert len(session.scalars(select(AgentRun)).all()) == len(MODES)
        assert not session.scalars(select(ConversationExecutionSlot)).all()
        for fixture, item in zip(fixtures, report, strict=True):
            assert fixture["mode"] == item["mode"]
            run = session.get(AgentRun, item["run_id"])
            conversation = session.scalar(select(Conversation).where(Conversation.external_id == fixture["conversation_id"]))
            assert run is not None and conversation is not None
            assert run.status == "done" and run.conversation_id == conversation.id
            events = session.scalars(select(AgentRunEvent).where(AgentRunEvent.run_id == run.id)).all()
            assert sum(event.event_type == "TOOL_CALL_START" for event in events) == 1
            assert sum(event.event_type == "TOOL_CALL_RESULT" for event in events) == item["result_count"]
            assert sum(event.event_type == "TOOL_CALL_ERROR" for event in events) == item["error_count"]
            messages = session.scalars(select(Message).where(Message.conversation_id == conversation.id)).all()
            assert len(messages) == 2 and {message.role for message in messages} == {"user", "assistant"}
    (OUTPUT / "database-evidence.json").write_text(json.dumps({
        "runs": len(MODES), "one_call_each": True, "messages_each": 2, "slots_released": True,
    }), encoding="utf-8")
    print("PASS PostgreSQL: seven runs, one Vault call each, two messages each, slots released.", flush=True)
