# 当前 Agent 应用架构

```mermaid
flowchart LR
    client[浏览器 / Next.js BFF / Swagger]
    router[FastAPI Routers]
    service[Chat Service]
    tools[本地工具白名单]
    model[DeepSeek API]
    repo[Repositories]
    postgres[(PostgreSQL + pgvector)]
    redis[(Redis)]

    client -->|HTTP / JSON| router
    router --> service
    service --> tools
    service -->|模型调用| model
    service --> repo
    repo --> postgres
    router -->|发布取消通知| redis
    service -->|订阅取消通知| redis
```

## 流式聊天与运行事件流

```mermaid
sequenceDiagram
    autonumber
    participant UI as ChatPanel（浏览器）
    participant BFF as Next.js BFF
    participant API as FastAPI
    participant DB as PostgreSQL
    participant LLM as DeepSeek
    participant Tool as Tool Registry

    UI->>BFF: POST /api/chat/stream {session_id, prompt}
    BFF->>API: POST /chat/stream（转发请求和取消信号）
    API->>DB: 创建 AgentRun + RUN_STARTED
    API-->>BFF: X-Run-ID + application/x-ndjson
    BFF-->>UI: X-Run-ID + 原样转发流
    UI->>UI: thinking（保存 run_id）

    loop Agent Loop 每一步
        API->>LLM: messages + tools
        alt 模型请求工具
            LLM-->>API: assistant.tool_calls
            API->>DB: TOOL_CALL_START
            API-->>BFF: TOOL_CALL_START
            BFF-->>UI: TOOL_CALL_START
            API->>Tool: 白名单校验并执行
            Tool-->>API: ToolObservation / ToolErrorObservation
            API->>DB: TOOL_CALL_RESULT / TOOL_CALL_ERROR
            API-->>BFF: TOOL_CALL_RESULT / TOOL_CALL_ERROR
            BFF-->>UI: TOOL_CALL_RESULT / TOOL_CALL_ERROR
            UI->>UI: 更新工具执行卡片
        else 模型给出最终回答
            LLM-->>API: assistant.content
            API->>DB: TEXT_MESSAGE_START / CONTENT / END
            API-->>BFF: TEXT_MESSAGE_START / CONTENT / END
            BFF-->>UI: TEXT_MESSAGE_START / CONTENT / END
            UI->>UI: streaming（追加最终文本）
        end
    end

    API->>DB: 保存完整 assistant 消息<br/>更新 Run 为 done + RUN_FINISHED
    API-->>BFF: RUN_FINISHED
    BFF-->>UI: RUN_FINISHED
    UI->>UI: done
```

### 取消信号传播

```mermaid
sequenceDiagram
    participant UI as ChatPanel（浏览器）
    participant BFF as Next.js BFF
    participant API as FastAPI 流任务
    participant DB as PostgreSQL
    participant Redis as Redis

    UI->>UI: 停止追加新文本
    UI->>BFF: POST /api/runs/{run_id}/cancel {reason}
    BFF->>API: POST /runs/{run_id}/cancel {reason}
    API->>DB: RUN_CANCELLATION_REQUESTED
    API->>DB: RUN_ABORTED 或 RUN_ERROR（与取消意图同一事务）
    API->>Redis: SET reason（5 分钟 TTL）+ PUBLISH
    Redis-->>API: 唤醒承载该 run 的实例
    API->>API: 取消流任务，触发 CancelledError
    alt reason = user
        API-->>UI: 流中断，UI 显示“已停止生成”
    else reason = timeout
        API-->>UI: 流中断，UI 显示超时提示
    end
```

## Agent Runtime（当前阶段）

```mermaid
flowchart LR
    model[DeepSeek 决策适配层] -->|ToolAction| loop[Agent Loop]
    model -->|FinalAnswer| answer[最终答案]
    loop --> registry[Tool Registry 白名单]
    registry --> validation[Pydantic 参数校验]
    validation --> executor[线程中的工具执行器]
    executor -->|成功| success[ToolObservation]
    registry -->|未知工具| failure[ToolErrorObservation]
    validation -->|非法参数| failure
    executor -->|异常或超时| failure
    success --> model
    failure --> model
    loop -->|达到最大步数| stopped[max_steps_exceeded]
```

当前已经实现 DeepSeek 决策适配层、完整循环和领域事件流。`POST /tool-test` 通过 `run_agent_loop` 获取兼容的最终结果；正式 `POST /chat/stream` 消费 `stream_agent_loop`，把工具开始、成功、失败与循环终态转换成可持久化、可传输的 NDJSON 事件。

```mermaid
sequenceDiagram
    autonumber
    participant API as POST /tool-test
    participant Decision as DeepSeekDecisionMaker
    participant LLM as DeepSeek
    participant Loop as Agent Loop
    participant Tool as Tool Registry

    API->>Loop: run_agent_loop(decision_maker)
    Loop->>Decision: decide(())
    Decision->>LLM: system + user + tools
    LLM-->>Decision: assistant.tool_calls
    Decision-->>Loop: ToolAction
    Loop->>Tool: 白名单解析、参数校验、执行
    Tool-->>Loop: ToolObservation / ToolErrorObservation
    Loop->>Decision: decide(累计 observations)
    Decision->>LLM: assistant.tool_calls + tool(tool_call_id, content)
    LLM-->>Decision: assistant.content
    Decision-->>Loop: FinalAnswer
    Loop-->>API: completed + answer + observations
```

适配层只负责供应商消息协议与 Runtime 决策之间的转换，不执行工具、不跳过白名单，也不解析或修复模型参数。当前采用每轮一个工具的顺序策略；如果模型一次请求多个工具，适配层会显式拒绝，避免静默丢弃调用。

运行时边界：

- 模型只能提出工具名和 JSON 参数，不能决定要执行的 Python 代码。
- 工具必须同时注册参数模型和执行器；未知工具与非法参数不会进入执行器。
- 同步工具在线程中运行，每个工具都有正数超时；普通异常会被转换为安全错误，取消则继续向上传播。
- 每次决策或工具结果后都重新进入循环，直到得到最终答案或达到最大步数。
- Runtime 只产生领域事件，不依赖 HTTP；Chat Service 负责事件落库和 NDJSON 编码，浏览器负责网络分块重组与状态渲染。
