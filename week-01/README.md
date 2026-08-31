# 第 1 周完成记录

周期：2026-08-24 ～ 2026-08-30

本文件只记录第一周结果。完整路线统一查看 [12 周学习计划](../LEARNING_PLAN.md)，当前断点查看 [学习交接](../LEARNING_HANDOFF.md)。

## 本周目标

使用 Python 和 FastAPI 完成可持久化的 LLM 对话接口，并理解最小 Tool Calling 执行过程。

## 已完成

- [x] Python 函数、列表、字典、异常、JSON 和类。
- [x] FastAPI 的 GET、POST、PUT、路径参数、查询参数和 Swagger。
- [x] 使用 Pydantic 校验请求体和响应体。
- [x] 使用 `AsyncOpenAI` 调用 DeepSeek。
- [x] 使用 `.env` 管理 `DEEPSEEK_API_KEY`。
- [x] 使用 `session_id` 隔离不同对话。
- [x] 实现最近 5 轮上下文窗口。
- [x] 使用 SQLite 保存并恢复历史消息。
- [x] 完成无参数工具 `get_current_time`。
- [x] 完成“模型选择工具 → Python 执行 → 模型生成最终回答”的闭环。

## 当前接口

| 接口 | 作用 |
|---|---|
| `GET /` | 检查服务是否启动 |
| `GET /chat` | 提示聊天接口使用 POST |
| `POST /chat` | 带会话记忆的 DeepSeek 对话 |
| `GET /sessions/{session_id}/messages` | 查询 SQLite 会话历史 |
| `POST /tool-test` | 测试时间工具调用 |

## 启动

环境配置和详细排错见 [环境与依赖](../ENVIRONMENT.md)。

```bash
cd apps/api
python -m uvicorn main:app --reload
```

打开 <http://127.0.0.1:8000/docs> 进行测试。

## 第一周结果

现在已经不只是调用一次模型 API，而是拥有了 Agent 最小雏形：会话状态、数据库记忆、工具说明、工具白名单、工具执行和最终回答。

下一阶段会先补齐 PostgreSQL、Redis、测试和分层结构，为第 3 周的 Next.js Agent 前端以及后续 Agent Runtime 提供稳定接口，安排见 [LEARNING_PLAN.md](../LEARNING_PLAN.md)。
