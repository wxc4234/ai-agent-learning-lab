# AI Agent 学习交接

更新时间：2026-09-15（Asia/Shanghai），今日已收尾。

这是新会话的唯一动态进度入口，长期规则见自动加载的 AGENTS.md。详细历史与验证记录按需查看，不再整篇加载计划或课程大纲。

## 当前目标与约束

- 正式进度：第 1～3 周完成，3 / 12（25%）；第 4 周本地工作台进行中。
- 主产品：本地优先 PC Coding Agent。Web UI/API/执行服务在用户电脑，模型由用户配置；无需产品注册登录。账号模式作为既有扩展保留，不主动推进访客系统或账号专项课程。
- 技术路线仍为 Next.js + FastAPI + PostgreSQL + Redis。域名用于展示、文档和分发；第 9 周保留独立受限样例的云部署学习。
- 用户要求在已检出 main 的主仓库学习。每次确认分支与工作区，不覆盖累计未提交改动。
- 一课一个可运行小任务，完整参考直接展示在对话中；已有文件只给完整改动段/函数，新文件才给整文件；四空格缩进，关键逻辑前说明原因和边界。
- 用户明确“完成了”后才检查实现、补测试和验收。测试和机械性配套由教练完成；口头答题不是课程门槛。
- 本轮用户直接授权了对话式侧栏/标题/历史、18px 样式和目录整理；后续课程恢复学习者编写核心实现，不能将这次授权当成永久代写授权。

## 当前界面与行为

- Codex 风格项目/任务侧栏，项目行有笔形新建入口和项目设置；目录选择及绑定放在设置中，不常驻项目 ID、时间和手填标题表单。
- 笔形入口只打开草稿，首次发送才创建 Task/Conversation；同任务后续消息使用同一 conversation_id。列表支持分页，选回任务读取真实历史。
- 第一轮完成后异步总结标题，失败保留首条消息摘录；后端重新授权并按旧标题条件更新，迟到结果不覆盖后来标题。
- 创建响应未确认时停止自动发送，可以从列表找回已创建的空任务；尚无服务端创建幂等。
- 空白态引导与输入框居中相邻，有消息后输入框固定底部；详情默认收起，可恢复三栏。左右布局开关独立于任务身份，收起导航不丢草稿。
- 全页正文、控件、提示基线 18px，标题更大；侧栏 320px、详情 340px。继续复用共享 shadcn/ui 控件和主题。
- Enter 发送，Shift+Enter 换行；输入法 composition/229 不误发。历史读取失败阻止发送，旧请求不会污染新任务。

## 已验收的后端与运行基线

- 第 1～3 周：FastAPI/模型调用、会话持久化、BFF NDJSON 透传、流式事件解析、六态聊天、工具注册/参数校验/Agent Loop、超时取消、最大步骤、run/event 时间线、指标与预算。具体边界见 docs/agent-ui-events.md 和 week-learning/week-03/REVIEW.md。
- 账号扩展：注册、登录、身份、退出与资源所有权。local 模式依赖稳定本机身份、Loopback Host、来源检查及服务端内部凭证；身份不替代资源授权。
- Workspace：创建、列表、原生目录选择和绑定/读取。选择目录不等于获得任意文件写入或命令执行权限。
- Task：模型、创建事务、HTTP/BFF、列表/分页/历史/标题。Task 与 Conversation 同事务创建；提交后响应失败可能返回错误但记录已存在，不能自动重发。
- 最后一课：TaskDetailResponse、task_detail 和 GET /workspaces/{workspace_id}/tasks/{task_id}。返回项目资料及任务/会话公开标识；定位不依赖列表第一页，检查项目和会话归属，只读且不返回 ORM 对象。

## 当前目录入口

后端按“层级 → 领域”组织，目录导航见 docs/project-structure.md。不要再向 routers/services/repositories 根层平铺新模块。

- HTTP：apps/api/app/routers/workspace/workspace.py
- Task 服务：apps/api/app/services/tasks/task_service.py、task_workspace.py
- Workspace 服务：apps/api/app/services/workspace/
- 模型与运行：apps/api/app/services/model/、services/runtime/
- 仓储：apps/api/app/repositories/{auth,chat,workspace,runtime}/
- 响应结构：apps/api/app/schemas.py；ORM：apps/api/app/models.py
- Task 测试：apps/api/tests/tasks/，公共夹具仍为 apps/api/tests/conftest.py
- 前端详情代理基础：apps/web/src/app/api/_shared/task-proxy.ts
- 工作台状态：apps/web/src/features/workbench/workbench-session.tsx
- 浏览器夹具：apps/web/test/browser/run-isolated.py、chat_test_app.py

目录整理移动了 97 个源码/测试文件，同步 Python 导入、测试包路径、浏览器脚本、迁移测试相对路径和文档引用；没有旧路径转发壳。应用启动入口仍为 app.main:app，不需要数据库迁移。

## 最近验证

- 详情课新增 18 条，与相关 Task 测试共 62 passed（5.41s）；核心无需修正，仅文件末尾换行。
- 目录整理后后端全量 899 passed（58.07s），-W error、Ruff 通过；前端 TypeScript 与 Workspace 325 条测试通过。目录迁移后浏览器主流程定向复跑 1 组通过，隔离资源已清理；命令见 ENVIRONMENT.md。
- 隔离 PostgreSQL 与 psycopg，使用随机独立测试库/私有 schema，允许真实提交，自动清理；禁止 SQLite 或测试开发业务表。
- 浏览器此前 4 组任务交互通过；18px 样式在 1366×768、1920×1080、2560×1318 验证。聊天/标题模型出口模拟，不宣称测试了真实模型生成质量。
- 开发库迁移 head=f16a53d928bc；已有验证 alembic check 无差异。Python 3.12.13 与环境细节见 ENVIRONMENT.md；跨电脑需各自安装依赖与迁移。

## 唯一下一课

**第 4 周：Task 详情 BFF。**

把已验收的 GET /workspaces/{workspace_id}/tasks/{task_id} 接到同源 /api/workspaces/[workspaceId]/tasks/[taskId]。校验项目/任务/会话公开标识与响应匹配，复用本地凭证、取消超时、错误脱敏和 no-store；与已有 task-proxy 列表/messages/title 兼容。

尚未提供下一课参考或实现。完成详情 BFF 后再推进 URL 选中状态与刷新自动恢复；不要重复创建服务、列表或详情 HTTP。最后一课已完成，用户今天停止学习。

## 尚未解决的问题

- 刷新后可手动选回历史任务，但 URL 自动选中恢复、任务删除及历史运行状态/时间线恢复尚未完成。
- 创建幂等、并发预算和短期状态仍待实现；刷新/重新进入可能丢失客户端未知结果提示。
- API 启动仍使用 init_db/create_all，新增模型可提前建新表但不更新旧表与 Alembic 账本。下一次新增迁移前先收口建表职责，不能直接 stamp 跳过缺失结构；此前 Task 空表修复过程见 ENVIRONMENT.md。
- 数据库提交与 Redis 通知不属于同一事务，重复取消不补发通知。同用户同会话并发排序、普通聊天异常后的缓存一致性仍待处理。
- 隔离取消曾出现 ASGI callable returned without completing response / Next failed to pipe response；页面停止和授权测试通过，但传输层有序关闭未排查完。
- 当前模型决策使用 stream=False，不可称为逐 Token 生成；文本出现后终态前断网的浏览器验证仍待完成，见 docs/chat-browser-fault-validation.md。
- 辅助标题调用费用尚未计入 AgentRun 指标。真实模型措辞质量没有在模拟浏览器测试中验证。
- 统一安装/依赖准备/迁移启动入口仍需交付阶段完善；源码版目前依赖 PostgreSQL/Redis，不宣称体验者已能零依赖安装。

## 按需文档

- 目录导航：docs/project-structure.md
- 环境、命令与验证证据：ENVIRONMENT.md
- 周大纲：LEARNING_CURRICULUM.md 第 4 周章节
- 教学规则：AGENTS.md；必要时查 LEARNING_COACH_GUIDE.md 对应章节
- 技能：FastAPI/BFF/流式状态先读 skills/agent-streaming/SKILL.md；工具/Agent Loop 先读 skills/agent-runtime/SKILL.md
- 面试题：interview-questions/；优先更新重要已有题目，参考答案已整理不等于已通过模拟面试。
