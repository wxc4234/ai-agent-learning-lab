# 第 3 周复盘：流式 Agent 产品闭环

## 本周完成

- 实现 FastAPI 流式接口与 Next.js BFF 流代理，浏览器不直接访问后端。
- 使用 `AbortController` 支持停止生成，并将取消信号传递到后端。
- 建立前端运行状态机：`idle`、`thinking`、`streaming`、`done`、`aborted`、`error`。
- 为超时、网络错误、500、502、429 等失败场景提供可读提示和重试入口。
- 接入 `AgentRun`、`AgentRunEvent` 与 `X-Run-ID`，记录一次运行的生命周期和文本片段。
- 补充 reducer 冒烟测试、事件流图，并通过 lint、类型检查和生产构建。
- 区分用户主动停止与请求超时：前者进入 `aborted`，后者进入 `error`，并把取消原因写入运行事件。
- 使用 Redis Pub/Sub 把取消信号传播到真正承载流任务的 API 实例，避免多实例部署下只能取消本机任务。
- 完成支撑真实工具过程 UI 的最小 Agent Runtime：带参数工具、Pydantic 校验、显式工具注册、结构化 Observation、最大步数、执行超时和取消传播。该内容已在 2026-09-10 的路线修订中正式归入第 3 周，不再记作第 7 周提前进度。
- 为 Agent Runtime 补充 9 个循环测试，并让后端完整测试达到 36 个。

## 这周理解最深的六个概念

1. 流式响应不是普通 JSON：BFF 必须原样转发 `response.body`，否则浏览器无法逐块渲染。
2. 取消是跨层能力：浏览器停止按钮、BFF 请求信号、FastAPI 的 `CancelledError` 必须形成同一条链路。
3. UI 状态与运行事件不同：UI 面向用户体验，运行事件面向可观测性和排障，两者需要明确映射。
4. 模型提供的工具名和参数都不可信：工具只能从显式白名单解析，参数必须经过 Pydantic 校验后才能进入执行器。
5. Agent Loop 不只是循环调用模型：还必须包含终止条件、最大步数、错误观察、工具超时以及取消向上传播。
6. 同步工具不能直接阻塞异步事件循环；使用工作线程执行后，FastAPI 仍能继续处理其他请求。

## 发现的工程问题

当前“用户主动停止”和“浏览器请求超时”都会通过 `AbortController` 中止请求。

前端会把超时显示为 `error`，但 FastAPI 收到取消后会记录为 `aborted`。这说明取消请求目前没有携带“取消原因”。

该问题已在本周后续完成：`POST /runs/{run_id}/cancel` 携带 `reason: "user" | "timeout"`，并通过 Redis Pub/Sub 将取消信号发送到承载流的 API 实例。现在前端状态、后端运行状态和数据库事件使用同一份终态语义。

## 复盘时的断点与下一步（历史记录）

以下内容保留第 3 周复盘当时的状态。到 2026-09-10，DeepSeek 决策适配、正式聊天 Agent Loop、工具事件卡片、成本与延迟指标、Token 预算均已在后续提交完成；当前断点请以 [../../LEARNING_HANDOFF.md](../../LEARNING_HANDOFF.md) 为准。

- 补充运行时间线的前端展示。
- 由学习者实现 DeepSeek 决策适配层，把模型消息转换为 `ToolAction` / `FinalAnswer`，并把工具 Observation 写回上下文。
- 由 Codex 补充 Mock 测试、类型修复和路由接线，随后验证一次真实的多步骤 Tool Calling。
- 后续再把工具执行事件接入现有 run timeline，在前端展示工具参数、执行状态和结果卡片。
