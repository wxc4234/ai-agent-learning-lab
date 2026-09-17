# AI Agent 学习交接

更新时间：2026-09-17（Asia/Shanghai），当日学习收尾；会话执行占用获取与释放事务服务已完成并验收。

这是新会话的唯一动态进度入口，长期规则见自动加载的 AGENTS.md。详细历史与验证记录按需查看，不再整篇加载计划或课程大纲。

## 2026-09-17 接续

- 学习者明确“完成了”后，已检查创建幂等事务服务。核心与参考一致；教练仅按 Ruff 合并等价嵌套 if、补文件末尾换行，并补测试和收尾文档。
- 已实现：先授权并锁项目、规范化标题和服务端 SHA-256、按用户/项目/键查询请求记录、同键重放/内容冲突/删除后拒绝重建、重新核对任务和会话归属、三条记录同事务提交。重放返回当前公开资料。
- 新增 37 条专项，随 Task/Workspace/local 相关回归共 545 条通过（41.45s，-W error）；后端全量 Ruff 与 diff check 通过。没有重复执行账号专项、前端或浏览器验收。
- 已启动 Docker Desktop，项目 PostgreSQL/Redis 健康；测试仅使用自动清理的随机独立库/schema，未迁移或修改开发业务表。详细命令与初次环境阻塞见 ENVIRONMENT.md。
- 本课 HTTP 接入核心与参考一致，无需修正。请求键暂时可选，严格正文校验；两种冲突返回安全 409，非法键返回 422，未知失败保持创建结果未确认。新增 37 条 HTTP 专项通过（3.83s），Task/Workspace/local 相关回归 582 条通过（42.15s，-W error）；Ruff/diff check 通过，隔离资源已清理。
- BFF 课按用户“这个你直接完成吧”授权由教练实现：严格校验并原样转发可选请求键，补 409/422 白名单，保留缺省/null 兼容和未确认语义，不生成键或自动重试。创建路由 92 条（新增 33）、Workspace 全量 612 条、TypeScript/ESLint/diff check 通过；上游 fetch 模拟，未运行浏览器/数据库。
- UI 课用户再次明确授权直接实现并要求解释。教练已接入工作台内存创建意图、首发请求键、冻结输入重试、冲突/删除拒绝及明确新意图；重试成功读历史，不自动发送消息。新增 4 组 PC 浏览器场景通过；提示修正后重跑关键恢复场景 1 组及既有首发/切换/列表找回/历史隔离 4 组通过，浅深色 PC 截图已视查。Workspace 612/聊天状态84、TypeScript/ESLint/diff check通过，临时服务及隔离数据库已清理。
- 本课学习者完成 ConversationExecutionSlot 模型与迁移，核心无需修正，只补迁移末尾换行；新增13条专项随迁移目录56条通过（7.42s），后端全量1205条通过（82.51s，-W error）；Ruff/diff check通过。开发库从0a7b64e039cd升级至1b8c75f140de，alembic check无结构差异。
- 本课学习者完成 conversation_execution_service.py，核心与参考一致，无需修正；教练新增 42 条专项，随 runtime/tasks/local 回归 508 条通过（36.91s，-W error），Ruff/diff check 通过。验证授权先行、短事务、真实锁等待、提交确认丢失及旧 token 不误释放；尚未接入聊天运行。
- 下一课是聊天执行生命周期中的占用接入；此前 UI 代写授权仅限对应课次，后续仍由学习者实现核心。继续在主仓库 main 学习，一课一个任务；本次按用户要求将当日累计源码、测试、迁移和对应文档统一提交并推送至 origin/main；正式进度仍为 3 / 12。

本次收尾范围：Task 创建幂等事务服务及 HTTP/BFF/UI 全链路、会话执行占用模型/迁移及获取/释放服务。验收证据已归档 ENVIRONMENT.md，课程范围与状态已同步 LEARNING_CURRICULUM.md、LEARNING_PLAN.md，重要考点已更新题库及索引。今天不继续接入运行生命周期；下次从“唯一下一课”开始。

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
- 创建响应未确认时停止自动发送，可以从列表找回已创建的空任务；创建服务、HTTP、BFF 和 UI 已接通请求键；未确认时同键重试找回任务，读历史后等待明确发送。
- 空白态引导与输入框居中相邻，有消息后输入框固定底部；详情默认收起，可恢复三栏。左右布局开关独立于任务身份，收起导航不丢草稿。
- 选中任务和首次创建后同步 URL；刷新及新标签页打开链接自动恢复任务与历史，继续使用原会话。草稿清除任务参数；不持久化未发送输入，不建立任务切换的浏览器前进/后退历史，不恢复历史运行摘要/时间线。
- 全页正文、控件、提示基线 18px，标题更大；左侧栏 320px。右侧详情默认 400px，支持 320～720px 拖动与方向键/Home/End 调宽、双击重置、localStorage 保存偏好，并根据窗口为中栏保留空间；当前运行与摘要改为分区/行式指标，数值不拆行。继续复用共享控件和主题。
- 空任务删除：学习者完成 Provider 状态、ref 防重、精确详情确认、迟到结果隔离及 URL 清理。教练按用户两张 Codex 截图调整任务行悬停删除图标、应用内确认框和项目菜单；保留 18px 字号。项目菜单仅接入新建任务、项目设置和刷新任务，不伪造归档/置顶/移除能力。
- Enter 发送，Shift+Enter 换行；输入法 composition/229 不误发。历史读取失败阻止发送，旧请求不会污染新任务。

## 已验收的后端与运行基线

- 第 1～3 周：FastAPI/模型调用、会话持久化、BFF NDJSON 透传、流式事件解析、六态聊天、工具注册/参数校验/Agent Loop、超时取消、最大步骤、run/event 时间线、指标与预算。具体边界见 docs/agent-ui-events.md 和 week-learning/week-03/REVIEW.md。
- 账号扩展：注册、登录、身份、退出与资源所有权。local 模式依赖稳定本机身份、Loopback Host、来源检查及服务端内部凭证；身份不替代资源授权。
- Workspace：创建、列表、原生目录选择和绑定/读取。选择目录不等于获得任意文件写入或命令执行权限。
- Task：模型、创建事务、HTTP/BFF、列表/分页/历史/标题。Task 与 Conversation 同事务创建；提交后响应失败可能返回错误但记录已存在，不能自动重发。
- Task 详情 HTTP：TaskDetailResponse、task_detail 和 GET /workspaces/{workspace_id}/tasks/{task_id}。返回项目资料及任务/会话公开标识；定位不依赖列表第一页，检查项目和会话归属，只读且不返回 ORM 对象。
- Task 详情 BFF：学习者完成同源 Task 详情 BFF、readTaskDetail 与 taskProxy 的 detail 分支。校验 URL 项目/任务与响应匹配、会话标识格式，重建公开字段；复用本地凭证、取消超时、错误脱敏和 no-store，兼容列表/messages/title。

- URL 恢复：学习者完成 task-url.ts、工作台 URL 恢复及恢复状态 UI。选中/创建后 replaceState 更新定位参数，刷新或复制链接通过详情恢复项目/任务，再由 TaskChat 读取历史；详情失败不回退成草稿，旧详情不得覆盖新选择，创建后保持组件 key。教练修正参考中 effect 同步 setState 的 lint 问题，补测试与隔离夹具。

- 空任务删除事务：学习者完成 task_deletion_service.py，仅删除没有消息、没有任何状态 Run 的空任务；按项目→任务→会话授权加锁，先删会话再删任务，同事务提交/回滚，保留项目与目录。核心无需修改，仅补末尾换行。现已接通 HTTP/BFF/UI；不代表已完成通用删除或运行生命周期收口。

- 本地会话保护：学习者收紧 conversation_repository.py：local 模式只允许已有 Task 会话，联查 Conversation/Task/Workspace 归属；创建 Run 前锁会话且不执行会话 INSERT，普通聊天在缓存/模型调用前重新检查。账号分支保留兼容。核心无需修正，仅补末尾换行。

- 最新完成：学习者实现空任务 DELETE HTTP，教练按本轮明确授权实现同源 DELETE BFF。成功 204 空正文；404 不可访问、409 已有历史；状态与错误码白名单脱敏，转发后超时/取消/异常提示删除结果未确认，不自动重试。该授权仅限本轮 BFF，后续核心课程仍由学习者实现。

- 任务运行历史查询：学习者完成 task_run_query.py 与两种响应结构；复用 owned_task 授权后按 Run ID 倒序读取 limit + 1 条，返回概要和下一页游标，不加载事件，不写入或提交。未知状态保留原值，结束时间为空则最终耗时为空。查询服务、HTTP 与 BFF 已验收，列表 UI 已接通（手动刷新，不订阅实时状态）。

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

- 2026-09-16 Markdown 展示修复：接入 react-markdown 10.1.0 + remark-gfm 4.0.1，新增 MarkdownMessage，历史助手消息与流式回复统一解析，用户消息保留纯文本。覆盖标题/强调/列表/任务列表/引用/代码/表格，代码表格内部滚动；禁用原始 HTML，沿用 URL 校验，图片显示显式链接。浏览器 3 组通过（逐块模拟流、格式/安全/滚动、历史恢复/浅深色），截图已视查；TypeScript、ESLint、聊天84/Workspace579及 diff check 通过。模型输出模拟，隔离服务和测试库已清理，下一课不变。

- 2026-09-16 右侧栏：按用户截图修正摘要依赖屏幕断点导致的三列挤压，改为标签/数值行式布局，移除当前运行嵌套卡片。右栏默认 400px，拖动/方向键/Home/End/双击调宽，持久化偏好并限制中栏空间。浏览器 9 组通过（原 8 组 + 真实聊天摘要/调宽），用模拟指标复现 757 Token、¥0.00128420、1499 ms，320px 下无断行；拖动保留草稿与节点、刷新恢复宽度。1366px 窄/宽截图已视查；Workspace 579、聊天 84、TypeScript、ESLint 通过，临时资源已清理。

- 2026-09-16 启动迁移职责：按用户明确授权移除 API 启动 create_all，新增 check_database_ready，只读比较迁移 heads，不一致拒绝启动；迁移 env 支持显式连接，浏览器隔离环境先执行真实 upgrade。新增 7 条专项通过，后端全量 1101 条通过（79.95s，-W error），Ruff/diff check 通过。开发库仅只读检查版本并通过，未迁移或写入。

- 2026-09-16 历史详情 UI：按用户明确授权完成 TaskRunPanel、列表选择入口及独立历史详情状态。返回保留列表，重试/返回/任务切换清理旧请求，文本由 React 转义。浏览器原 5 组列表 + 新增 3 组详情共 8 组通过，1366/1920×900 截图已视查；Workspace 579 条、聊天状态 84 条、TypeScript、全量 ESLint 和 diff check 通过。真实空任务链路，其余分页/详情/故障模拟；临时服务和隔离数据库已清理，无真实模型调用。

- 2026-09-16 运行详情 BFF：用户明确授权直接完成 GET /api/runs/[runId]、run-detail-proxy.ts 与 run-detail-data.ts。新增 61 条、Workspace 全量 579 条及聊天状态 84 条通过，TypeScript、ESLint、夹具 Ruff 与 diff check 通过。复用流事件解析器，兼容持久化开始/取消事件，未知事件保留信封但 payload 置空；核对 Run ID、事件 ID 严格递增和时间/耗时，重建公开字段。测试直接调用路由、模拟上游，无浏览器/数据库/模型调用；隔离启动器已补复制新路由。

- 2026-09-16 运行历史 UI：学习者完成右侧概要列表、分页、刷新、失败重试及任务切换取消/旧回调隔离。教练恢复被替换掉的 RunSummaryCard 并补文件换行；隔离启动器补复制 runs 路由。新增浏览器 5 组全部通过（真实空任务 BFF/API，其余分页和故障响应模拟），1366/1920 PC 截图已视查；Workspace 518 条、聊天状态 84 条、TypeScript、ESLint、夹具 Ruff 和 diff check 通过。临时服务及独立数据库/schema 自动清理。

- 2026-09-16 运行列表 BFF：按用户本轮明确授权完成 task-run-data.ts、task-run-proxy.ts 和 runs/route.ts；新增 79 条路由专项、Workspace 全量 518 条通过，TypeScript、全量 ESLint 与 diff check 通过。覆盖内部凭证隔离、参数重复/越界、项目/任务匹配、排序/游标、公开字段重建、错误脱敏及请求/正文/JSON 完成阶段取消超时。直接调用真实 GET 路由，上游 fetch 模拟；未运行浏览器、数据库或模型。授权仅限本课 BFF，后续核心仍由学习者实现。

- 2026-09-16 运行列表 HTTP：核心实现与参考一致，无需修改。新增 test_task_run_api.py 42 条通过（4.53s）；Task/runtime/local/workspace 共 602 条通过（44.08s），-W error；全量后端 Ruff 与 diff check 通过。覆盖真实分页、跨会话隔离、空任务授权、非法参数、访问边界、SQL/响应异常脱敏与 no-store；隔离库/schema 自动清理。未接 BFF/UI，未运行浏览器或模型。

- 2026-09-16 运行列表查询：新增 test_task_run_query.py，44 条隔离 PostgreSQL 专项通过（2.67s），覆盖分页、归属、状态时间、无事件读取/写入/commit、真实 SQL 错误与 Session 清理。核心无需修正，仅补文件末尾换行。Task/runtime/local/workspace 共 560 条通过（38.31s），-W error；后端全量 Ruff 与 diff check 通过。独立测试库/schema 已清理，命令见 ENVIRONMENT.md。

- 2026-09-16 删除 UI：新增 task-delete.mjs 共 10 个场景分别验收通过；最终布局调整后重复验证删除/菜单 5 组及既有首发/项目切换/历史失败与旧请求隔离 3 组，全部通过。列表 DOM 保持/延迟骨架专项 1 组通过；Workspace 439 条、聊天状态 84 条、TypeScript、全量 ESLint 与 diff check 通过。原删除核心仅修正两处缩进，后续按明确授权调整样式和加载体验。浅/深色 PC 截图已视查，临时服务与独立数据库已清理，命令见 ENVIRONMENT.md。

- 2026-09-16 删除 HTTP/BFF：HTTP 新增 42 条通过，后端 tasks/workspace/local 共 422 条通过（30.91s），-W error 与领域 Ruff 通过；BFF 新增 43 条、Workspace 全量 439 条通过，TypeScript 与 ESLint 通过。真实浏览器删除链路 1 组通过（空任务 204、重复 404、历史任务 409 并保留消息），隔离资源已清理；命令见 ENVIRONMENT.md。

- 2026-09-16 本地会话课：新增 40 条隔离 PostgreSQL 专项通过（4.65s），受影响 local/tasks/chat API/stream/run repository 共 189 条通过（14.21s），-W error；Ruff、修改脚本 ESLint/语法及 diff check 通过。真实工作台 first send 1 组与本地模式 3 组浏览器通过，模型出口模拟，临时服务和测试库自动清理。旧 local-mode.mjs 已补先创建项目、进入草稿、展开运行详情的配套。
- 2026-09-16 空任务删除课：新增隔离 PostgreSQL 专项 27 条（2.14s），Task 领域共 116 条通过（9.14s），-W error；领域 Ruff 与 diff check 通过。独立连接验证提交事实，真实 SQL 错误验证回滚；pg_blocking_pids 验证 Message/Run 插入与删除的锁竞争，覆盖删除提交/回滚及插入先提交。测试库/schema 自动清理，未改开发业务表、迁移或前端。
- 2026-09-16 URL 课：新增 URL 单元测试 13 条，Workspace 全量 396 条、聊天状态 84 条通过；TypeScript、全量 ESLint、夹具 Ruff、diff check 通过。真实 BFF/API/隔离 PostgreSQL 的 PC 浏览器 8 组全部通过，覆盖自动刷新/新标签页恢复、同会话续聊、首发不中断、恢复失败与重试、分页外定位、项目列表失败独立恢复、旧详情/历史竞态。模型出口模拟；1366/1920 截图已视查。临时服务和测试库已清理。Docker Desktop 与项目 PostgreSQL/Redis 已启动并健康，未执行开发库迁移或重置。
- 2026-09-16 详情 BFF 新增 58 条，连同既有读取代理共 73 条通过；Workspace 全量 383 条通过，TypeScript、全量 ESLint 和 diff check 通过。核心无需修正，仅补新路由末尾换行。直接调用实际 GET 路由，上游 fetch 模拟；取消/超时覆盖请求、正文读取与 JSON 完成阶段。本轮未运行浏览器、数据库或模型调用。
- 详情课新增 18 条，与相关 Task 测试共 62 passed（5.41s）；核心无需修正，仅文件末尾换行。
- 目录整理后后端全量 899 passed（58.07s），-W error、Ruff 通过；前端 TypeScript 与 Workspace 325 条测试通过。目录迁移后浏览器主流程定向复跑 1 组通过，隔离资源已清理；命令见 ENVIRONMENT.md。
- 隔离 PostgreSQL 与 psycopg，使用随机独立测试库/私有 schema，允许真实提交，自动清理；禁止 SQLite 或测试开发业务表。
- 浏览器此前 4 组任务交互通过；18px 样式在 1366×768、1920×1080、2560×1318 验证。聊天/标题模型出口模拟，不宣称测试了真实模型生成质量。
- 开发库迁移 head=1b8c75f140de；本课验证 alembic check 无差异。Python 3.12.13 与环境细节见 ENVIRONMENT.md；跨电脑需各自安装依赖与迁移。

本课验收（2026-09-16）：修正模型表名缺少 s、primary_key 参数拼写；新增请求迁移专项 17 条，迁移目录 43 条通过。后端全量 1118 条通过（75.03s，-W error），Ruff 与 diff check 通过。开发库已从 f16a53d928bc 升级至 0a7b64e039cd，alembic check 无结构差异；回退只在隔离测试库演练。请求键在真实空任务删除后保留。交接、课程、计划与重要题库已同步。

## 唯一下一课

**第 4 周：聊天执行生命周期中的占用接入。**

ConversationExecutionSlot 模型、迁移及获取/释放短事务服务已完成。服务先授权并锁住已有会话，再通过 INSERT ON CONFLICT 原子获取；释放同时匹配会话与持有者 token。服务返回普通数据，拒绝接管已有事务，数据库错误保留原分类；占用不自动过期。目前尚无实际聊天执行拦截。

下一课先核对普通聊天与流式 Agent 的入口、副作用及执行收尾，确定占用获取和释放位置，再给出一个完整可验证的接入任务。须覆盖启动失败、流未开始及取消后的清理责任，不能只在某个入口加锁而留下另一入口绕过。终态写入、取消协程或 to_thread 等待取消均不证明所有执行器已经停止；只有实际执行及必要收尾结束后才能释放。进程崩溃恢复与遗留占用清理单独处理。

Task 创建幂等已端到端接通，UI 内存意图不跨刷新保存。后续核心默认由学习者实现；尚未展示下一课参考或提前创建测试。

## 尚未解决的问题

- URL 自动选中与消息历史恢复已完成；空任务删除服务、HTTP/BFF/UI 已完成。本地会话已禁止隐式重建；取消终态仍不等于协程停止，通用删除和历史运行恢复仍待完成。
- 按用户明确要求修复加载闪烁：三栏 WorkbenchShell 移到 keyed TaskChat 外层，右侧详情通过 Portal 跟随当前任务；ProjectGroup 改为稳定项目 key，保留展开状态及已有列表，revision 重置分页并后台刷新；确认删除后排除旧列表目标。会话仍重新读取真实历史，250ms 后才显示静态骨架，快请求不显示加载文字；未引入会话缓存，读取失败仍禁止发送。
- 删除结果未确认时保留查询入口并阻止再次删除；详情 200 也不证明之前的删除已经终止，刷新页面仍会丢失该客户端提示。
- 创建幂等端到端已接通；执行占用模型及获取/释放服务已完成，生命周期接入、并发预算和短期状态仍待实现。刷新/关闭会丢失内存中的创建重试键及未发送内容，不能将跨刷新恢复视为已完成。
- API 启动已改为只读迁移版本检查，结构变更须显式 Alembic upgrade。版本一致不保证没有手工结构漂移，不能直接 stamp 跳过缺失结构；此前 Task 空表修复过程保留在 ENVIRONMENT.md。
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
