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

## 为什么会话执行占用不能直接用 Run.status 表示？

复习优先级：高；参考答案已整理、尚未模拟。

当前取消接口会先写入 Run 终态，再通知执行协程；终态不证明执行器和必要收尾已经停止。如果只查 running 就允许新运行，取消与清理之间会出现重叠。因此将业务结果与执行占用分开：conversation_execution_slots 使用 conversation_id 作主键，每个会话最多一条；owner_token 标记当前持有者；acquired_at 只用于观察，不能据此推断执行失效。

现有服务先授权，再原子获取占用，释放时同时匹配 conversation_id 和 owner_token。仅按会话删除会让旧执行的迟到清理误删新占用。token 是防误释放标记，不是用户身份，也不是能阻止旧工具继续写入的 fencing token。进程崩溃会遗留记录；未确认旧执行停止前不能靠短 TTL 或随意删行放行新执行。

目前已完成模型、迁移及获取/释放服务，尚无运行拦截。外键不级联删除，防止删除会话时静默清除占用；已有空任务删除遇到占用会整体回滚，但友好的业务冲突映射需在后续接入时补齐。历史 Run 不回填占用，因为状态无法证明是否仍在执行。主键已提供唯一索引，无需重复索引，也不复制 user_id。

证据：tests/migrations/test_conversation_execution_slot_migration.py，覆盖带旧 Task/Conversation/Run 的升级/回退/再升级、非空/格式/外键/唯一约束、ORM 真实提交与时间字段，以及真实空任务删除因占用外键失败而完整回滚；并发竞争已由获取/释放服务专项验证，实际执行生命周期接入仍待完成。

## 为什么获取占用只用短事务，释放还必须匹配 token？

复习优先级：高；参考答案已整理、尚未模拟。

参考答案：会话行锁只在数据库事务内有效，用于串行化授权后的获取/释放操作；提交后的占用记录才代表跨越模型和工具执行时间的持有关系。不能让数据库事务一直等待模型返回。获取采用 INSERT ON CONFLICT DO NOTHING RETURNING，仅明确的占用冲突转换为 conversation_busy；连接或 SQL 故障不能冒充“会话忙”。服务拒绝已有事务，避免意外提交或回滚调用方的其他工作；提交前构造普通返回值，不依赖提交后 ORM 自动刷新。

释放使用 conversation_id 与 owner_token 两个条件。A 已释放、B 已获取后，A 的迟到清理应返回 False，不能删除 B 的占用。格式正确的 token 仍不能替代资源授权；授权检查先于占用访问。取消已经写入终态、占用时间很久都不是自动释放的证据。

失败场景与取舍：数据库已提交但确认丢失时，获取可能报错而占用已经存在；后续获取必须仍拒绝。释放确认丢失后再次释放可以返回 False。当前选择保守保留占用，避免未知执行重叠；并未实现崩溃后的自动恢复，也不保证获取失败后客户端能找回 token。

项目证据：app/services/runtime/conversation_execution_service.py；tests/runtime/test_conversation_execution_service.py 新增 42 条，覆盖授权顺序、错误 token、既有事务不受干扰、SQL/提交前后失败、返回值构造失败、旧占用与取消终态，以及通过 pg_blocking_pids 确认的真实锁等待。获取/释放分别提交或回滚时验证竞争结果，并验证不同会话独立获取；随 runtime/tasks/local 共 508 条通过。
