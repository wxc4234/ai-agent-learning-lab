# AI Agent 学习交接

更新：2026-09-28（Asia/Shanghai）。这是唯一动态接续入口；只保留当前快照，禁止追加历史课程流水账。教学与维护规则见 [AGENTS.md](AGENTS.md)。

## 当前状态

- 正式进度：第 1～4 周完成，4/12；第 5 周进行中。完整任务与阶段能力见 [课程大纲](LEARNING_CURRICULUM.md)。
- 主产品：本地优先 PC Coding Agent；Next.js + FastAPI + PostgreSQL + Redis，模型由用户配置。账号模式保留为扩展。
- 主仓库使用 `main`；每次接续重新检查分支与工作区，不把历史验收视为当前 checkout 已复验。
- 最新工程完成：普通项目写入前置检查只读HTTP接口及严格协议/归属/状态变化/失败未知验收通过。用户授权教练直接实现核心、测试与收尾；不标记为学习者独立掌握。
- 本次收尾提交汇总自cc8d430以来的Task差异/固定验证/受控编码闭环、PC工作台精简、普通项目许可策略/持久化/HTTP/BFF/PC联合验收及前置检查HTTP。用户已授权提交并推送main；接续以Git实际HEAD和origin/main为准，不继承后续提交推送授权。

## 唯一下一课

**第 5 周：普通项目写入前置检查的同源BFF。**

- 目标：代理只读assessment接口，严格校验入参与资源回执，固定分类白名单投影；本机运行令牌只在服务端注入。
- 验收：参数/Origin/上游协议异常安全拒绝，资源错配和意外eligible不透传，断网与取消保留未知，不自动重试。
- 范围：BFF与定向测试，不接入UI/自动调用，不领取应用机会、不修改许可或文件。
- 教学：下一课在新会话开始，恢复默认学习分工：提供完整核心参考实现，等待学习者完成后再检查与补测试；前课直接完成授权不自动延续。
- 入口：后端project_write_grants.py的assessment及现有许可BFF；改动前读取skills/agent-streaming/SKILL.md。

## 接续所需边界

- 补丁模块只处理内存，路径检查仅是语法检查；固定 a/b 同路径文件头，不支持 Git 扩展头、时间戳、CRLF、重命名或文件新增/删除。空文件可编辑，同一位置的多块插入须合并，不自动偏移/模糊匹配。已有字符串替换审阅 Diff 使用 JSON 转义，不能直接作为本模块输入。

- 本地请求的 run_command：样例 ready 使用独立只读快照，missing 使用普通无挂载沙箱，busy/sealed 不提供命令能力；查询失败不降级。两条路径均固定镜像、无网络，不开放普通项目挂载。
- `/workspace` 为只读样例挂载，WorkingDir 保持 `/tmp`。样例进程内登记只接受同一对象；`run_sample_sandbox_command` 仅接受可信内部来源，普通命令路径仍拒绝 bind，模型参数没有增加来源字段。
- 只读诊断分别观察来源与容器，不提供跨资源原子快照；`identity_matches_record` 不证明内容未变或隔离配置仍有效，也不赋予清理/执行权。未登记对象及仅有路径的部分现场不读取文件系统；未知 ID 只接受 created 候选发现，原恢复记录不被改写。
- 来源核对不是与 Docker 共享的原子事务；只支持无外部写者的自建样例。创建中途失败附带自有现场定位；失败/取消不自动删除来源，不自动重试。调用者须保存恢复信息，进程重启不恢复样例句柄或执行权，尚无持久恢复入口。
- 当前命令链实机适配为 macOS Docker Desktop 固定 socket/API 1.45；不据此声称 Windows 命令执行链已验收。
- 命令成功要求 exited、退出码 0，且 OOM/daemon 错误均明确为 False；工具完成不等于进程成功，未知不能补成成功。取消停止证据不等于完整结果，清理需确认对象缺失。
- 命令恢复记录仍为进程内存，重启丢失；没有公开恢复/清理接口。样例来源持久记录不恢复原进程句柄、执行权或重试权。
- 提案审批、应用占用和文件副作用是不同状态；受限样例已完成应用闭环，普通项目写入尚未开放。摘要核对、只读 preflight、原子替换都不是文件条件比较交换，不能消除核对后的外部修改。
- `ready`、`identity_matches_record` 仅为读取快照，不赋予执行、清理或恢复权限。查询失败保留未知，身份仍须在真正操作边界重新授权。

## 最近验收摘要

- 新增`POST .../write-grant/assessment`，仅接收grant_id、revision和明确apply_requested；复用应用级宿主，身份来自本地认证，不接收调用方可信事实。响应仅资源外部ID和固定拒绝分类，不提供执行凭据。
- 入参严格拒绝额外字段/类型转换/查询参数；复用local/Origin/JSON门禁，资源不可访问404。未知读取、宿主缺失和非法内部响应均500 `project_write_assessment_read_failed`，不会变成grant_missing或许可变更未知；响应no-store。意外eligible按协议失败处理。
- [HTTP专项](apps/api/tests/workspace/proposals/test_project_write_assessment_api.py)43项与受共享请求/错误边界影响的既有许可测试14项，共57项通过（38项不相关既有用例未运行）。真实隔离PostgreSQL/临时文件核对正常目标仍exclusive_access_unconfirmed、重复检查、撤销/修订/旧宿主/绑定/inode变化、应用已消耗、越权及异常；许可、应用状态/token、文件字节/inode/mtime不受检查修改。
- 改动文件Ruff、Pyright及diff空白检查通过；未运行全量回归或前端浏览器验收，未新增迁移，没有模型/Docker调用或普通项目写入。本课接口尚无BFF/UI入口。
- 既有PC许可联合验收证据仍见[专项](apps/web/test/browser/project-write-grant-integration.mjs)及[独立DB对账](apps/web/test/browser/project_write_grant_fixture.py)，最近两轮冷启动通过；本课未重跑，首次撤销404原因仍待定位。

## 未解决问题

- 许可联合验收首次旧修订撤销出现404，后续两轮独立环境返回预期409；已保留响应正文诊断，尚未定位首次原因，不宣称已修复。

- 刷新/关闭仍会丢失未发送草稿、创建重试键及删除结果未确认的客户端提示；一次详情成功不证明先前写请求已停止。
- 跨进程并发总预算、持久 Checkpoint 和副作用恢复尚未实现；本机退出恢复拒绝无身份旧数据、外机及停止证据不足的情况，不自动重放工具。
- 数据库提交与 Redis 通知不是同一事务，重复取消不补发通知；跨系统恢复待处理。
- 隔离取消曾出现 ASGI/Next 响应管道关闭错误；传输层有序关闭及文本出现后终态前断网仍待验证，见 [浏览器故障验收](docs/chat-browser-fault-validation.md)。
- 模型流式未结束时无法确认最终用量；失败/取消轮次仍无完整指标，不推算为零。辅助标题费用未计入 AgentRun，受控模型测试不能证明真实模型措辞质量。
- 通用 Apply Patch、普通项目的Git/目标测试与整周编码冒烟闭环未完成；受控样例工具与PC联合闭环已完成。自动依赖安装与分发尚未交付，源码运行仍需 PostgreSQL/Redis。
- API 启动只读核对迁移版本，迁移须显式执行；版本一致不证明没有手工结构漂移，禁止用 stamp 掩盖缺失结构。

## 按需入口

- [目录导航](docs/project-structure.md)、[环境与隔离测试](ENVIRONMENT.md)：定位代码或运行时再查。
- [课程大纲](LEARNING_CURRICULUM.md)：只读当前周对应任务；调整路线才读 [主计划](LEARNING_PLAN.md)。
- [历史学习分工](docs/history/learning-through-2026-09-23.md)、[历史验收](docs/history/verification-through-2026-09-23.md)：先搜索主题，再读命中章节，不全文加载。
- [面试题库](interview-questions/README.md)：保留重要题目、参考答案与项目证据；已归档不等于已通过模拟面试。
