"""PC 隔离夹具：只控制模型出口，真实授权、扫描、向量存储和流协议。"""

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import AgentRun, AgentRunEvent, Conversation, ConversationExecutionSlot, Message, Task, Workspace
from app.services.auth.local_identity import resolve_local_identity
from app.services.chat import chat_service
from app.services.model.streaming_model_decision import StreamingDeepSeekDecisionMaker
from app.services.runtime.agent import code_search_binding as binding
from app.services.workspace.files.code_batch_generation import generate_and_save_code_batch
from tests.model.response_stream import build_text_response, build_tool_response
from tests.model.test_code_embeddings import config, response
from tests.model.test_model_decision import build_usage
from vault_search_model import snapshot

OUTPUT = Path(__file__).resolve().parents[2] / "output/playwright/code-search"
MODES = ("match", "empty", "error", "cancel")


def vector_snapshot(session):
    # 比较整行而非计数，防止刷新时偷偷覆盖已有批次或向量。
    return {
        name: session.scalars(text(f"SELECT to_jsonb(t)::text FROM {name} t ORDER BY id")).all()
        for name in ("code_embedding_batches", "code_embedding_vectors", "code_embedding_spaces")
    }


def install_code_search_fixture(app):
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        OUTPUT.mkdir(parents=True, exist_ok=True)
        for path in OUTPUT.iterdir():
            if path.suffix in (".json", ".png"):
                path.unlink()
        fixtures, streams, queries = [], [], []
        chat_calls: dict[str, int] = dict.fromkeys(MODES, 0)
        cancelled = closed = False
        previous_maker, previous_client = chat_service.DeepSeekDecisionMaker, chat_service.client
        previous_config, previous_factory = binding.load_embedding_config, binding.make_search_code_definition
        usage = build_usage(prompt_tokens=10, completion_tokens=10, cache_hit_tokens=0, cache_miss_tokens=10)

        class SlowBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                nonlocal cancelled
                (OUTPUT / "cancel-started.json").write_text('{}')
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    cancelled = True
                    raise
                yield b'{}'

            async def aclose(self):
                nonlocal closed
                closed = True

        def embed(request):
            query = json.loads(request.content)["input"][0]
            mode = next(mode for mode in MODES if f"[{mode}]" in query)
            queries.append(mode)
            if mode == "cancel":
                return httpx.Response(200, headers={"content-type": "application/json"}, stream=SlowBody())
            if mode == "error":
                return httpx.Response(503, json={"error": "PRIVATE_PROVIDER_DETAIL"})
            return httpx.Response(200, json=response())

        async def respond(**kwargs):
            prompt = next(item["content"] for item in reversed(kwargs["messages"]) if item["role"] == "user")
            mode = next(mode for mode in MODES if f"[{mode}]" in prompt)
            chat_calls[mode] += 1
            assert "search_code" in {item["function"]["name"] for item in kwargs["tools"]}
            observations = [item for item in kwargs["messages"] if item["role"] == "tool"]
            if not observations:
                stream = build_tool_response((f"code-{mode}", "search_code", json.dumps({"query": f"[{mode}] 查找取消函数"})), usage=usage)
            else:
                assert len(observations) == 1 and mode != "cancel"
                value = json.loads(observations[0]["content"])
                if mode == "match":
                    hit = value["matches"][0]
                    assert hit["relative_path"] == "src/取消.py" and hit["start_line"] == 1
                    assert "def cancel_task" in hit["text"]
                    answer = f'取消函数返回停止状态。来源：`{hit["relative_path"]}:{hit["start_line"]}`。这是本次检索的代码快照。'
                elif mode == "empty":
                    assert value["status"] == "not_found_in_window" and not value["matches"]
                    answer = "最近检索窗口没有兼容的代码快照，不能据此判断整个项目没有相关代码。"
                else:
                    assert value["type"] == "tool_error" and value["error"]["details"] == "code_search_unavailable"
                    assert "PRIVATE_PROVIDER_DETAIL" not in observations[0]["content"]
                    answer = "本次代码检索失败，不能据此判断没有相关代码。"
                stream = build_text_response(answer, usage=usage)
            streams.append(stream)
            return stream

        with TemporaryDirectory(prefix="code-browser-") as directory:
            parent = Path(directory).resolve()
            async with original(application):
                with SessionLocal() as session:
                    user_id = resolve_local_identity(session).id
                for mode in MODES:
                    root = parent / mode
                    (root / "src").mkdir(parents=True)
                    (root / "src/取消.py").write_text('def cancel_task():\n    """取消当前任务。"""\n    return "stopped"\n', encoding="utf-8")
                    workspace_id, task_id, conversation_id = (uuid4().hex for _ in range(3))
                    # 归属先提交；后续真实扫描/模型请求不占此事务。
                    with SessionLocal() as session, session.begin():
                        workspace = Workspace(external_id=workspace_id, name=f"代码检索-{mode}", user_id=user_id, root_path=str(root))
                        task = Task(external_id=task_id, title=f"查找取消函数-{mode}", workspace=workspace)
                        session.add_all([workspace, task, Conversation(external_id=conversation_id, user_id=user_id, task=task)])
                    if mode != "empty":
                        await generate_and_save_code_batch(
                            user_id=user_id, workspace_id=workspace_id, task_id=task_id, config=config(),
                            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response(len(json.loads(request.content)["input"])))),
                        )
                    fixtures.append({"mode": mode, "workspace_id": workspace_id, "task_id": task_id, "conversation_id": conversation_id})
                before = snapshot(parent)
                with SessionLocal() as session:
                    vectors = vector_snapshot(session)
                    bindings = [(row.external_id, row.root_path, row.binding_revision) for row in session.scalars(select(Workspace).order_by(Workspace.id))]
                # 保留生产 StreamingDecisionMaker，包括真实 messages/tools 装配和发送守卫。
                binding.load_embedding_config = config
                binding.make_search_code_definition = lambda **kwargs: previous_factory(**kwargs, transport=httpx.MockTransport(embed))
                chat_service.DeepSeekDecisionMaker = StreamingDeepSeekDecisionMaker
                chat_service.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=respond)))
                (OUTPUT / "fixtures.json").write_text(json.dumps(fixtures), encoding="utf-8")
                try:
                    yield
                finally:
                    binding.load_embedding_config, binding.make_search_code_definition = previous_config, previous_factory
                    chat_service.DeepSeekDecisionMaker, chat_service.client = previous_maker, previous_client
                    assert snapshot(parent) == before
                    assert queries == ["match", "error", "cancel"]
                    assert chat_calls == {"match": 2, "empty": 2, "error": 2, "cancel": 1}
                    assert cancelled and closed and all(stream.closed for stream in streams)
                    assert application.state.execution_budget.in_use == 0
                    with SessionLocal() as session:
                        assert vector_snapshot(session) == vectors
                        assert [(row.external_id, row.root_path, row.binding_revision) for row in session.scalars(select(Workspace).order_by(Workspace.id))] == bindings
                    (OUTPUT / "server-evidence.json").write_text(json.dumps({
                        "files_unchanged": True, "bindings_unchanged": True, "vectors_unchanged": True,
                        "chat_calls": chat_calls, "query_calls": queries, "embedding_cancelled": cancelled,
                        "embedding_closed": closed, "chat_streams_closed": True, "execution_budget_released": True,
                    }), encoding="utf-8")
        assert not parent.exists()
        (OUTPUT / "cleanup-evidence.json").write_text(json.dumps({"project_directories_removed": 4}))

    app.router.lifespan_context = lifespan


def verify_code_rows(engine):
    report = json.loads((OUTPUT / "evidence.json").read_text())
    fixtures = json.loads((OUTPUT / "fixtures.json").read_text())
    assert len(report) == len(fixtures) == 4
    with Session(engine) as session:
        assert len(session.scalars(select(AgentRun)).all()) == 4
        assert not session.scalars(select(ConversationExecutionSlot)).all()
        for fixture, item in zip(fixtures, report, strict=True):
            mode = fixture["mode"]
            assert item["mode"] == mode
            run = session.get(AgentRun, item["run_id"])
            conversation = session.scalar(select(Conversation).where(Conversation.external_id == fixture["conversation_id"]))
            assert run is not None and conversation is not None
            assert run.conversation_id == conversation.id and run.status == ("aborted" if mode == "cancel" else "done")
            events = session.scalars(select(AgentRunEvent).where(AgentRunEvent.run_id == run.id)).all()
            assert sum(event.event_type == "TOOL_CALL_START" for event in events) == 1
            assert sum(event.event_type == "TOOL_CALL_RESULT" for event in events) == (1 if mode in ("match", "empty") else 0)
            assert sum(event.event_type == "TOOL_CALL_ERROR" for event in events) == (1 if mode == "error" else 0)
            messages = session.scalars(select(Message).where(Message.conversation_id == conversation.id)).all()
            assert len(messages) == (0 if mode == "cancel" else 2)
    (OUTPUT / "database-evidence.json").write_text(json.dumps({"runs": 4, "messages": 6, "slots_released": True}))
    print("PASS code search PostgreSQL: 4 runs, 6 messages, cancelled run not saved, slots released.", flush=True)
