# 一次 Agent 运行需要记录什么，如何按 `run_id` 还原时间线？

> 主题：后端 / Agent 可观测性 | 频率：高 | 关联学习：`LEARNING_CURRICULUM.md` 第 3 周 Day 6

## 考点

- 能否区分会话消息和一次 Agent 运行。
- 能否设计有序事件、终态和耗时记录。
- 能否在不泄露敏感信息的前提下定位失败。

## 核心答案

一次 Agent 运行不等于一条 assistant 消息。运行记录保存整体状态，事件记录运行过程：开始、文本分块、工具调用、完成、中止和错误。`run_id` 是一次运行的稳定标识，所有事件通过外键关联到同一条运行记录，并按自增事件 ID 排序还原时间线。

运行表至少需要：`run_id`、会话归属、当前状态、开始时间和结束时间。事件表至少需要：事件 ID、`run_id`、事件类型、结构化 payload 和创建时间。终态只能是 `done`、`aborted` 或 `error`，结束时计算 `finished_at - started_at` 得到耗时。

事件日志是事实来源，前端状态、调试界面和评测报告都可以从它投影出来。开始事件只记录必要元数据，例如会话标识和问题长度，不直接写入密钥或未经脱敏的完整用户内容。

## 结合项目怎么讲

当前项目使用 `agent_runs` 和 `agent_run_events` 两张表。流式请求创建运行并写入 `RUN_STARTED`；每个文本块写入 `TEXT_MESSAGE_CONTENT`；正常结束写入 `RUN_FINISHED`，用户取消写入 `RUN_ABORTED`，模型或服务异常写入 `RUN_ERROR`。通过 `GET /runs/{run_id}` 可以读取状态、耗时和按顺序排列的全部事件。

## 追问清单

1. 为什么不能只保存最终 assistant 消息？
2. 事件为什么要单独建表？
3. 如何防止事件日志泄露用户输入和密钥？
4. 流开始后发生错误，如何记录？
5. `run_id` 和 `session_id` 的职责有什么不同？
6. 服务重启后能否继续查询历史运行？

## 简化记忆

```text
session_id：哪段会话
run_id：哪一次执行
event：执行过程中发生了什么
status：最终结果
duration：花了多久
```

## 运行取消如何同时保证所有权、幂等与终态互斥？

复习优先级：高；参考答案已整理、尚未模拟。

参考答案：run_id 仅用于定位，不能当作凭证。读取时间线和取消时，使用 CurrentUser.id，通过 AgentRun.conversation_id 连接 Conversation，并同时限制 run_id 与 Conversation.user_id。未知和非本人统一 404；取消必须先校验归属，再检查终态，否则可能向他人暴露“运行已经完成”的信息。未登录为 401，数据库故障为脱敏 500。

取消在一个 PostgreSQL 事务内锁住 AgentRun 行，检查 running，写 RUN_CANCELLATION_REQUESTED、对应终态事件以及 finished_at。正常结束也锁同一行，并在取得锁后重新检查状态。只有第一个有效终态写入者改变状态；重复取消返回 204，不重复插入事件或发布通知。单纯把几次写入放进事务，并不能防止两个事务同时读到 running。

授权与事件写入成功、事务提交后才发布 Redis 通知。事件落库失败应回滚状态，且不通知。数据库与 Redis 不是同一事务：发布失败返回 503，但数据库可能已经提交终态；当前重复取消不会补发通知。要实现可靠投递需要额外的重试/事务消息设计，当前不能宣称 exactly-once 或可靠通知补偿已实现。

前端取消 BFF 验证 Origin/JSON，只转发唯一合法 Cookie，并保留取消信号和 no-store；错误正文使用安全映射。页面区分 401、404、服务故障与请求中断，最终停止本地接收。AbortController.abort() 不能证明服务端已取消，因此失败提示说明“未确认服务端取消”；晚返回的取消响应通过请求版本号避免污染新一轮页面状态。

项目证据：app/repositories/runtime/run_repository.py、app/routers/runtime/runs.py、run_boundary.py；test_run_ownership.py 使用真实 Cookie 和独立 PostgreSQL，覆盖双用户、匿名旧数据、终态拒绝、回滚、行锁等待、双取消竞争与 Redis 故障。前端 cancel-run-route.test.ts、run-terminal.test.ts 与隔离浏览器取消场景覆盖真实生产函数和同源 BFF。具体验收结果见 ENVIRONMENT.md 的本课记录。

追问：只在取消函数加行锁是否足够？为什么重复取消不应新增事件？提交后 Redis 失败，客户端应该如何描述结果？用户在另一个页面登出后，本页停止按钮会发生什么？
