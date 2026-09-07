# 当前 Agent 应用架构

```mermaid
flowchart LR
    client[Swagger / 后续 Next.js 前端]
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

    UI->>BFF: POST /api/chat/stream {session_id, prompt}
    BFF->>API: POST /chat/stream（转发请求和取消信号）
    API->>DB: 创建 AgentRun + RUN_STARTED
    API-->>BFF: X-Run-ID + text/plain 流
    BFF-->>UI: X-Run-ID + 原样转发流
    UI->>UI: thinking（保存 run_id）

    API->>LLM: stream=True
    loop 每个文本片段
        LLM-->>API: delta
        API->>DB: TEXT_MESSAGE_CONTENT
        API-->>BFF: delta
        BFF-->>UI: delta
        UI->>UI: streaming（追加文本）
    end

    API->>DB: 保存完整 assistant 消息<br/>更新 Run 为 done + RUN_FINISHED
    API-->>BFF: 流结束
    BFF-->>UI: 流结束
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

当前已实现 `Agent Loop → Registry → 参数校验 → 工具执行 → Observation`。DeepSeek 决策适配层仍是下一课，正式接入前 `/tool-test` 继续保留原有的两次模型调用演示。

运行时边界：

- 模型只能提出工具名和 JSON 参数，不能决定要执行的 Python 代码。
- 工具必须同时注册参数模型和执行器；未知工具与非法参数不会进入执行器。
- 同步工具在线程中运行，每个工具都有正数超时；普通异常会被转换为安全错误，取消则继续向上传播。
- 每次决策或工具结果后都重新进入循环，直到得到最终答案或达到最大步数。
