# 第五周复盘：Coding Tools 与安全执行

2026-09-29 完成原定整周目标和扩展产品范围。用户授权教练实现；以下是工程验收，不表示学习者已独立掌握或通过模拟面试。

## 交付范围

- 授权文件读取、搜索、精确补丁、风险策略与审批；模型不能自行批准或获得宿主执行权限。
- 普通项目单文件真实应用，原文备份、事务审计、反向提案恢复；同意和实际执行分离。
- 通用变更组涵盖新增、更新、删除、重命名；整组基线检查、持久日志、失败恢复、进程中断后显式恢复以及并发排除。
- 持久隔离内容工作区：独立任务内修改，导出到原任务的待审批变更组，确认丢失后查询同一导出记录。
- 普通项目有界快照命令与自定义验证；Git 暂存差异 API/BFF/PC，loose/自包含 pack 与引用叶对象校验。
- PC 审阅、审批、应用和恢复闭环，以及固定编码任务集。支持范围、预算与限制统一见[执行协议](../../docs/project-execution.md)和[暂存协议](../../docs/project-staged-api.md)。

## 验收证据

以下是按改动选择的独立批次，存在重叠，不相加冒称全量测试。

| 范围 | 本轮结果与入口 |
|---|---|
| 文件生命周期、恢复与既有应用兼容 | 变更组、普通写入、隔离副本及既有迁移联合 42 项通过；`apps/api/tests/workspace/proposals/` 对应专项 |
| 写前外部修改 | 最后新增的定向用例1项通过：不覆盖外部内容，不误报已恢复；`test_baseline_changed_before_prepare_remains_uncertain` |
| 新 HTTP 门禁及扩大快照 | 选定 12 项通过，含真实 Docker 大项目验证；`test_change_set_api.py`、`test_project_snapshot.py` |
| 新 BFF 协议 | `apps/web/test/features/workspaces/change-set-routes.test.ts`：24 项通过 |
| 工具选择 | `test_request_task_sample_tools.py`：17 项通过 |
| 迁移、应用互斥、删除与载体 | 选定 20 项通过；`test_change_set_migration.py` 等直接受影响测试 |
| 既有编码闭环 | `scripts/verify_project_coding_loop.py`：服务层5项、真实 Agent Loop 6项分别通过；报告 `apps/web/output/playwright/week5-smoke/` |
| PC 联合流程 | `apps/web/test/browser/week5-completion.mjs`：副本四类操作→导出→原任务批准/应用→整组恢复；1366/1920桌面无 pageerror |
| 独立持久化核对 | `week5-completion/database.json` 确认原项目恢复、副本保留、导出后再批准；两组终态分别 applied、rolled_back |
| 类型、静态与迁移 | 受影响 Pyright、Ruff、前端 typecheck/ESLint 通过；开发库升级 d7c8a61be54f，alembic check 无新增操作 |

浏览器报告与截图位于被忽略的 `apps/web/output/playwright/week5-completion/`；复跑入口在执行协议及环境说明，不把产物存在等同于任意后续 checkout 已验收。旧单文件 PC 应用/反向恢复入口 `project-apply.mjs` 另行通过。

模型决策使用确定性测试替身，实际经过 Agent Loop、工具、事件、数据库、文件系统及容器。没有真实模型调用；不能据此推断自然语言任务成功率或真实费用。

## 关键取舍

数据库与文件系统不能共享事务，所以先持久记录再产生副作用，保留真实原文件，事后核对并显式恢复。跨文件执行有可恢复日志，不提供全组原子可见性；外部编辑或未知现场必须拒绝覆盖。

目录级会话锁跨越执行生命周期但不跨文件 I/O 持有数据库事务；旧单文件路径采用同键非阻塞锁，避免和资源行锁相互等待。锁约束内部请求，不能代表外部编辑器停止。

持久隔离工作区采用内容副本；导出需要源基线仍匹配，并将候选与导出回执在同一事务提交。副本不是 Git worktree，临时命令产物不会自动写回。

文本格式、现有父目录和大小预算属于当前明确支持协议。命令使用固定 Python 镜像，不自动安装依赖；macOS 已实测，Windows 未实测。命令执行恢复记录仍在内存，和本周新增的持久文件变更恢复不是同一个能力。安装分发、通用 Runtime Checkpoint 等仍按原课程安排推进；历史偶发404按用户要求待复现处理。
