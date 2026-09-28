# AI Agent 学习交接

更新：2026-09-28（Asia/Shanghai）。这是唯一动态接续入口；只保留当前快照，禁止追加历史课程流水账。教学与维护规则见 [AGENTS.md](AGENTS.md)。

## 当前状态

- 正式进度：第 1～4 周完成，4/12；第 5 周进行中。完整任务与阶段能力见 [课程大纲](LEARNING_CURRICULUM.md)。
- 主产品：本地优先 PC Coding Agent；Next.js + FastAPI + PostgreSQL + Redis，模型由用户配置。账号模式保留为扩展。
- 主仓库使用 `main`；每次接续重新检查分支与工作区，不把历史验收视为当前 checkout 已复验。
- 最新工程完成：用户授权的全仓库类型与弃用维护，覆盖生产代码、测试和脚本；统一 Python 检查入口，修复可空值、异步生成器契约与过期测试夹具。最近学习课仍为 Git 样例 diff 受控只读采集，学习者按参考实现核心；工程验收不等同于独立掌握。
- 用户已授权将 Git diff 采集与本轮类型维护一起提交、推送；实际同步状态以 Git 为准。模型公开文本流式传输与对话定位已保留，历史验收见对应测试与 UI 事件设计。

## 唯一下一课

**第 5 周：Task 作用域内的 Git 样例 diff 读取。**

- 目标：在既有 TaskGitSamples 中复用登记身份、每次读取重新授权与借用互斥，调用本课内部 diff 采集；只接受固定 worktree/staged 范围。
- 验收：合法已登记样例返回对应差异；未登记、跨任务、归属变化、关闭/借用竞争及底层采集失败明确失败；采集结束或异常均释放借用，关闭不能提前删除目录。
- 范围：内部 Task 服务与直接测试，不注册模型 diff 工具、不接 HTTP/UI、不解析或应用补丁、不开放普通项目 Git。基线仍只由可信临时夹具准备。
- 教学：先给完整核心参考；学习者明确“完成了”后补测试与验收。只回归新增和直接受影响功能；未经授权不执行 Git 提交。
- 入口：`services/workspace/git/task_git_samples.py`、`diff_capture.py` 与对应 `tests/workspace/git/`。

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

- 全仓库静态检查：Python 476 个文件，Pyright basic + 弃用诊断 0 错误/警告；后端、测试和脚本 Ruff 通过；前端 typecheck、lint 通过。复现入口见 [环境说明](ENVIRONMENT.md#5-定向测试与静态检查) 与 [类型配置](pyrightconfig.json)。TS/JS/MJS 206 个文件弃用扫描保留一处输入法 keyCode 229 兼容判断，原因与 MDN 依据见环境说明。
- 本轮统一回归覆盖 158 个实际受影响测试文件：4752 passed、30 skipped，启用 warnings-as-errors；跳过项为需显式启用的真实 Docker 测试。这是受影响范围的同轮结果，不代表全量后端或所有平台验收。
- 关键失败路径新增证据：[来源绑定丢失时提前拒绝](apps/api/tests/tools/test_request_task_sample_tools.py)、[发现缺失身份保持未知](apps/api/tests/runtime/sandbox/test_sandbox_sample_reconciliation.py)。历史迁移夹具按当时表集合重建，数据库测试使用隔离 PostgreSQL；未触碰开发业务数据。
- [diff 采集](apps/api/tests/workspace/git/test_diff_capture.py) 与 [status 采集](apps/api/tests/workspace/git/test_status_capture.py) 纳入本轮回归，覆盖真实临时 Git、子进程、超时/超限及管道缺失回收。仍仅支持 POSIX 可信自建样例，不含 Task diff 授权、普通项目或补丁应用；空 diff 不证明仓库干净，暂存比较要求已有 HEAD。
- 本轮未复验真实 Docker、供应商模型、Windows 或浏览器交互；静态检查与替身测试不能替代这些边界。
- 前次流式/滚动证据入口：[模型专项](apps/api/tests/model/test_streaming_model_decision.py)、[聊天专项](apps/api/tests/chat/test_chat_incremental_text.py)、[UI 事件设计](docs/agent-ui-events.md)、[隔离模型夹具](apps/web/test/browser/typewriter_model.py)。前次真实隔离页面覆盖增量 Markdown、停止、刷新不重播、发送/重试定位与手动上翻保持；供应商未实测，本课未复验。
- Git 状态 PC 四场景及历史无重放证据见 [专项脚本](apps/web/test/browser/git-status.mjs) 与本地忽略产物 `apps/web/output/playwright/git-status/`；本课未复验。其他前期能力见课程大纲、对应测试与题库，不汇总历次测试数为全量结论。

## 未解决问题

- 刷新/关闭仍会丢失未发送草稿、创建重试键及删除结果未确认的客户端提示；一次详情成功不证明先前写请求已停止。
- 跨进程并发总预算、持久 Checkpoint 和副作用恢复尚未实现；本机退出恢复拒绝无身份旧数据、外机及停止证据不足的情况，不自动重放工具。
- 数据库提交与 Redis 通知不是同一事务，重复取消不补发通知；跨系统恢复待处理。
- 隔离取消曾出现 ASGI/Next 响应管道关闭错误；传输层有序关闭及文本出现后终态前断网仍待验证，见 [浏览器故障验收](docs/chat-browser-fault-validation.md)。
- 模型流式未结束时无法确认最终用量；失败/取消轮次仍无完整指标，不推算为零。辅助标题费用未计入 AgentRun，受控模型测试不能证明真实模型措辞质量。
- 通用 Apply Patch、Git/目标测试工具与整周编码冒烟闭环未完成。自动依赖安装与分发尚未交付，源码运行仍需 PostgreSQL/Redis。
- API 启动只读核对迁移版本，迁移须显式执行；版本一致不证明没有手工结构漂移，禁止用 stamp 掩盖缺失结构。

## 按需入口

- [目录导航](docs/project-structure.md)、[环境与隔离测试](ENVIRONMENT.md)：定位代码或运行时再查。
- [课程大纲](LEARNING_CURRICULUM.md)：只读当前周对应任务；调整路线才读 [主计划](LEARNING_PLAN.md)。
- [历史学习分工](docs/history/learning-through-2026-09-23.md)、[历史验收](docs/history/verification-through-2026-09-23.md)：先搜索主题，再读命中章节，不全文加载。
- [面试题库](interview-questions/README.md)：保留重要题目、参考答案与项目证据；已归档不等于已通过模拟面试。
