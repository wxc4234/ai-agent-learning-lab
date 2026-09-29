# 普通项目执行与写入

本地产品提供受控直接写入，以及持久隔离副本。模型只能生成候选；用户在 PC 改动面板审阅、批准，再显式应用。阶段验收见[第五周复盘](../week-learning/week-05/REVIEW.md)，当前接续只见[交接](../LEARNING_HANDOFF.md)。

## 单文件提案

原单文件链路继续使用 `.../file-edit-proposals/{proposal_id}/write-grant/apply`，只接受 `grant_id` 和 `revision`。审批、发放许可与应用分别进行，浏览器经同源 BFF，运行令牌仅由服务器注入。

服务重新授权完整资源链，检查目录/文件身份、绑定修订、原字节和候选摘要；领取事务保存原文备份并消费一次机会，然后才执行文件副作用。原子替换保留元数据，写后核对实际对象与字节，最后登记终态。`proposal_audit_events` 与各业务状态变更同事务提交。

PC“应用审计与恢复”查询公开事件；`POST .../write-grant/restore-proposal` 只接受 `{"action":"restore"}`。原应用为 applied、备份正确、文件仍匹配应用后摘要时，生成反向 pending 提案，再经过审批、许可和应用。重复恢复请求返回同一反向提案；不重放原应用。未知原应用、外部变化、备份缺失均拒绝。

## 通用变更组与整组恢复

模型工具 `create_change_set` 一次提交 1～16 个结构化操作：`create` 新增 UTF-8 文本、`update` 精确 LF 统一补丁、`delete` 删除、`move` 重命名并保留内容和元数据。路径相对项目，父目录须已存在；拒绝重复/交叠目标、已有重命名目标、Git 元数据、符号链接、特殊文件、多硬链接和不支持的文件元数据。单文件最多256 KiB、总补丁输入1 MiB、新旧内容合计2 MiB、完整审阅 Diff 64 KiB；超限整体拒绝。二进制、模糊匹配、自动创建目录与任意 Git 扩展补丁不属于此文本协议。

先观察整组基线，全部验证通过才同事务保存 `TaskChangeSet`。PC“查询变更组”展示最近50组完整 Diff，再显式批准/拒绝、应用或检查恢复。旧 `create_patch_proposals` 仍保留为已有文件的独立提案兼容入口。

执行持有 PostgreSQL **会话级目录锁**，使用 autocommit 连接，不跨文件 I/O 持有数据库事务；锁从领取前持续到终态登记。旧单文件链也使用同一个目录键，并通过非阻塞事务锁避免“持 Workspace 行锁等待目录锁”的死锁。running/uncertain 阻止同根其他应用；任务删除和目录重绑也检查新的在途状态。

目标写入前先保存持久 journal，创建完整新文件并记录对象身份。原文件 inode 移入项目内私有 `.agent-changes-*` 恢复目录，保留 ACL/xattr/mode；新文件用拒绝覆盖的 link 发布。发生普通故障时尝试整组恢复，进程中断后用户也可显式恢复。恢复先检查整组对象/内容，再逆序还原；支持识别 link 完成、unlink 尚未完成的中断现场。已恢复终态可重复查询，不重放原应用。

检测到外部编辑、删除、对象替换、未知现场或持久化失败时保留 uncertain，不覆盖冲突、不假装完成回滚。会话锁不能阻止外部编辑器；这是一组带恢复日志的操作，不是跨文件原子事务或文件系统 CAS。HTTP 取消等待不证明服务器已停止。备份目录属于执行证据，文件工具/命令副本排除它，不能当作项目源码提交；现阶段保留恢复记录和副本，相关任务明确拒绝删除，避免清掉归属后留下不可恢复现场。

## 持久隔离工作区

PC“隔离工作区”显式创建独立 Workspace、Task 和 Conversation。内容副本放在用户主目录的 `.ai-agent-learning-lab/workspaces/`，数据库保存源任务、源绑定修订、初始内容和副本目录身份；跨请求、刷新和服务重启保留。使用独占目录与无跟随描述符复制，浏览器/模型不能指定宿主路径。

副本是独立内容工作区，不冒称 Git worktree；不复制 Git、依赖、环境文件或恢复现场。可在副本正常聊天、生成变更组并审批应用。导出逐文件比较初始基线与当前副本，核对原项目未发生冲突，只在原任务创建 pending 变更组。导出候选和导出回执同事务提交，确认丢失后可查询同一结果；每份副本固定一次导出记录，继续任务可从新的项目状态创建新副本。导出不会自动批准或写回原项目。副本及源任务均重新授权；绑定/身份变化、二进制修改、超出变更组预算等整体拒绝。

## 项目命令与验证

聊天 `run_command` 在普通绑定项目中创建有界独立快照，复用固定镜像、无网络、非 root、资源/输出上限与停止/清理确认。原项目不挂入容器；只读载体在容器 `/tmp/project` 展开，命令可以修改临时副本，不写回宿主。持久隔离工作区的文件修改仍走审批工具，单次命令临时产物不自动导出。

模型仅提交容器绝对程序路径的 argv 和项目相对 working_directory，不选择镜像、挂载、宿主环境或策略。命令准备前后重新核对当前会话/任务，快照前后核对项目绑定。未绑定时保留无挂载容器；样例 ready 使用原样例快照，busy/sealed 或读取失败不降级。

项目预算为2,048文件、8 MiB正文、单文件2 MiB、深度16、最多128个子目录及单目录1,024个条目；载体上限16 MiB。排除 `.git`、`.venv`、`venv`、`node_modules`、`__pycache__`、`.next`、`.env*`、`.agent-changes-*`。链接、特殊文件、观察到的变化与超限整体拒绝。固定 Python 镜像支持其中已经安装的程序；可运行项目 unittest、编译/语法检查和自定义命令，不自动联网安装依赖，也不声称提供所有语言工具链。非零退出、截断、OOM与daemon错误保留真实结果。命令恢复 journal 仍在进程内；它与持久的变更组恢复记录是不同能力。

## Git 与验证入口

`verify_project_git` 在授权作用域内读取 HEAD/index 引用的叶对象，验证 zlib、类型、长度和SHA-1；支持 loose/自包含pack。最多256个唯一叶对象、8 MiB解压正文；gitlink、缺失、损坏及不支持格式拒绝。它不证明工作树干净。暂存 API/BFF/PC 的范围见[暂存协议](project-staged-api.md)。

- 一条命令编码冒烟：在 `apps/api` 执行 `../../.venv/bin/python -m pytest ../../scripts/verify_project_coding_loop.py -q -s`。服务层与真实 `stream_agent_loop` 覆盖查找、单/跨文件、拒绝、失败检查、危险命令；报告位于 `apps/web/output/playwright/week5-smoke/`。决策为测试替身，真实工具/事件/Observation，模型 token/费用为null。
- 通用文件生命周期、故障点、进程中断、并发、越权：`test_change_sets.py`；持久副本/冲突导出：`test_owned_areas.py`；新增HTTP门禁：`test_change_set_api.py`；大项目真实容器：`test_project_snapshot.py::test_large_project_uses_dedicated_carrier_budget`。
- 完整PC链路：沿用[环境说明](../ENVIRONMENT.md)隔离浏览器启动器，设置 `BROWSER_TEST_SCRIPT=week5-completion.mjs`。实际经过模型测试替身、Agent Loop、BFF/API、PostgreSQL和文件；报告 `apps/web/output/playwright/week5-completion/{browser,database}.json`，两种桌面截图。单文件原链路另有 `project-apply.mjs`。
- 迁移 `d7c8a61be54f` 新增变更组与隔离工作区，已有记录时拒绝降级；`test_change_set_migration.py` 与既有迁移模型比较检查对应持久化边界。
