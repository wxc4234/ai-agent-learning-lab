# 项目目录导航

## 按什么顺序找文件

先确定应用，再确定层级和业务领域。以任务详情为例：HTTP 入口在 `routers/workspace/workspace.py`，读取服务在 `services/tasks/task_workspace.py`，对应测试在 `tests/tasks/test_task_detail.py`。

## 后端

```text
apps/api/
    app/
        main.py                 # 应用装配和生命周期
        config.py               # 配置
        database.py             # 引擎、Session 和 Base
        dependencies.py         # 请求身份依赖
        local_boundary.py       # 本地访问边界
        models.py               # ORM 模型
        schemas.py              # HTTP/事件数据结构
        routers/
            auth/               # 账号扩展：注册、登录、身份、退出
            chat/               # 对话、历史和聊天请求边界
            workspace/          # 项目、目录、任务 HTTP 接口
            runtime/            # 运行、取消和工具接口
            system/             # 健康检查
        services/
            auth/               # 本地身份及账号凭证/会话服务
            chat/               # 对话编排与消息持久化协调
            workspace/          # 创建、目录校验、系统目录选择
            tasks/              # 任务创建、详情、列表、标题
            model/              # 模型客户端、决策适配和计价
            runtime/            # 按运行时职责继续分组
                agent/          # Agent Loop、Token 预算、工具上下文与事件
                execution/      # 会话占用、并发、线程、取消及进程恢复
                command/        # 命令契约、环境、有界输出与异步读取
                sandbox/        # 容器策略、身份和生命周期
                docker/         # Docker 客户端与 attach 协议
        repositories/
            auth/               # 用户和登录会话持久化
            chat/               # 会话与消息持久化
            workspace/          # 项目查询与归属检查
            runtime/            # 运行/事件持久化
        tools/
            registry.py         # 工具注册及同步/异步执行定义，Runtime已按类型分派
            run_command.py      # 异步受限命令适配，已接收显式恢复记录，已在local流式聊天通过请求级能力快照注册
    tests/
        conftest.py             # 所有领域共享的隔离 PostgreSQL 夹具
        auth/
        chat/
        core/                   # 配置、健康检查、数据库隔离
        local/
        migrations/             # Alembic 兼容性验证
        model/
        runtime/                # agent/execution/command/sandbox/docker 对应服务分组
        tasks/
        tools/
        workspace/
    migrations/
        versions/               # 保留 Alembic 按修订顺序管理的脚本
```

分层职责不变：路由管理 HTTP 边界，服务编排业务，仓储负责数据访问。业务分组用于导航，不要求每个领域都机械建立所有层，也不为一个辅助函数再加一层文件夹。

`models.py`、`schemas.py` 和运行配置仍是明确的公共入口，本轮不为目录美观拆散 ORM 注册与协议类型；后续确有独立演进需要再按领域拆分。前端已经采用 `src/features/` 领域结构与 Next.js `app/` 路由结构，继续沿用。

## Runtime 内部导航与依赖

`services/runtime/` 根目录只保留包说明，具体模块按职责存放。文件名保留业务含义，测试采用相同子目录；运行 HTTP/仓储测试仍放 `tests/runtime/` 根目录。

| 要处理的问题 | 入口与阅读顺序 |
|---|---|
| Agent 决策与工具循环 | `agent/agent_runtime.py` → `token_budget.py`、`tool_event_payloads.py`、`tool_wait.py`（预算来源及取消收尾）；工具授权上下文见 `tool_execution_context.py` |
| 执行占用与收尾 | `execution/conversation_execution_scope.py` → `conversation_execution_service.py`、`execution_threads.py` |
| 执行容量、取消与恢复 | `execution/execution_budget.py`、`run_cancellation.py`；命令恢复记录见 `command_recovery_store.py`；恢复见 `execution_recovery.py` → `execution_process.py` |
| 命令输入与输出 | `command/command_contracts.py` → `command_output.py` → `command_stream.py` → `command_capture.py`；环境见 `command_environment.py` |
| 沙箱策略与生命周期 | `sandbox/sandbox_spec.py`、`sandbox_identity.py`、两个 `*_policy.py`；操作按 creation → reconciliation → start → stop → exit/cleanup 阅读；`sandbox_execution.py` 协调订阅、启动、输出及失败停止收尾；`sandbox_cleanup.py` 提供独立created/exited显式清理入口；`sandbox_command_result.py` 将已确认执行结果适配为公开命令契约；`sandbox_command.py` 为创建/执行/清理统一内部入口，保留阶段恢复证据；`sandbox_command_reconciliation.py` 只读核对失败执行的当前状态；`command_recovery_journal.py` 保存作用域内有界恢复记录 |
| Docker 传输与帧解析 | `docker/docker_client.py`、`docker/docker_attach_parser.py`、`docker/docker_attach_stream.py`；分别负责客户端、帧解析和异步排空；`docker/docker_attach_http.py` 负责固定HTTP请求构造及响应头读取与校验；`docker/docker_attach_connection.py` 持有连接、握手及关闭收尾 |

依赖约束：`command` 不依赖 Docker 或沙箱；`sandbox` 的策略模块依赖命令契约，生命周期模块调用 `docker` 客户端。Docker 客户端使用 `sandbox_spec` 的创建契约，但不反向导入生命周期服务。`agent` 使用 `execution` 的线程跟踪能力，`execution` 不依赖 Agent 循环。包的 `__init__.py` 只描述职责，不集中导出模块或执行装配，避免扩大导入副作用。

新增模块按上述职责归位，不再向 runtime 根目录平铺，也不为了命名一致创建空子目录。目录整理后的attach HTTP握手、启动前订阅与结果适配已完成，统一创建/执行/清理入口已完成，模型工具注册尚未接入。

## 导入与测试

使用完整领域路径，不在旧层级留下转发模块。例如：

```python
from app.services.tasks.task_workspace import task_detail
from app.routers.workspace import workspace
```

跨目录复用测试夹具采用明确包路径，例如：

```python
from tests.local.test_local_mode import HEADERS
```

从 `apps/api` 运行：

```bash
../../.venv/bin/python -W error -m pytest -q
../../.venv/bin/python -W error -m pytest -q tests/tasks
../../.venv/bin/python -m ruff check app tests
```

浏览器启动器继续位于 `apps/web/test/browser/run-isolated.py`，导入后端的新路径。数据库连接、隔离和清理规则见根目录 `ENVIRONMENT.md`。

## 文档与学习记录

- `LEARNING_HANDOFF.md`：当前事实、下一课和未解决问题，保持精简。
- `LEARNING_CURRICULUM.md`：课程学习内容、阶段完成情况与待完成能力。
- `ENVIRONMENT.md`：环境变化、运行命令和真实验收记录。
- `week-learning/`：已结束周次的练习与复盘。
- `interview-questions/`：重要问题、参考答案和项目证据。

新增文件前先判断它的生命周期与业务领域。跨周源码、测试和配置继续放 `apps/`，不放进当周学习目录。

命令结果展示：`apps/web/src/features/chat/command-result-view.ts` 校验公开结果并保守判定状态；`components/tool-result.tsx` 展示独立有界输出区及通用回退。专项测试位于 `apps/web/test/features/chat/command-result.test.ts`，PC真实命令复用 `test/browser/command-tools.mjs`。

跨目录文件查找：`apps/api/app/services/workspace/workspace_find.py` 在授权后进行描述符递归，固定扫描、深度、路径与结果预算；测试位于 `apps/api/tests/workspace/test_workspace_find.py`。工具适配已通过 `apps/api/app/tools/find_files.py` 注册为需要可信上下文的能力，专项见 `apps/api/tests/tools/test_find_files_tool.py`。

文件查找PC验收：`apps/web/test/browser/find-tools.mjs` 创建并清理临时项目，`find_model.py` 仅控制模型决策；通过既有 `run-isolated.py` 运行真实BFF/API/文件服务/隔离数据库。

文本替换预览：`apps/api/app/services/workspace/workspace_edit_preview.py` 仅执行纯内存唯一替换与有界审阅Diff；测试位于 `apps/api/tests/workspace/test_workspace_edit_preview.py`。授权文件读取已由 `workspace_file_preview.py` 组合，测试见 `apps/api/tests/workspace/test_workspace_file_preview.py`；仍无文件写入。

文件修改预览工具：`apps/api/app/tools/preview_file_edit.py` 需要可信上下文，公开有界Diff及原文基线，不回传完整修改后文件；专项测试 `apps/api/tests/tools/test_preview_file_edit_tool.py`。此工具只读，无写入/审批能力。

修改预览PC验收：`apps/web/test/browser/preview-tools.mjs` 创建真实临时文件并逐场景核对字节不变，`preview_model.py` 仅控制模型决策，复用隔离浏览器启动器。

预览卡片：`apps/web/src/features/chat/file-edit-preview-view.ts` 校验公开协议，`components/file-edit-preview-card.tsx` 展示只读状态和Diff；组件分派由 `tool-result.tsx` 负责。测试 `file-edit-preview.test.ts` 和 `command-result.test.ts` 共用 `render-tool-result.ts` 编译真实组件，浏览器沿用preview-tools.mjs。

文件修改提案：`apps/api/app/models.py` 的 FileEditProposal 与 `migrations/versions/4eb108c473ab_add_file_edit_proposals.py` 管理待审批记录；`repositories/workspace/file_edit_proposal_repository.py` 负责归属锁和flush，`services/workspace/file_edit_proposal_service.py` 负责读文件前后的事务边界及提交。配套为 `tests/workspace/test_file_edit_proposal_service.py`、`tests/migrations/test_file_edit_proposal_migration.py`。当前不开放HTTP/工具或文件写入。

提案授权查询复用上述workspace仓库/服务文件：`read_owned_file_edit_proposal` 单条联表授权并仅选择公开列，`get_task_file_edit_proposal` 返回只读审阅快照；专项位于 `apps/api/tests/workspace/test_file_edit_proposal_query.py`。查询不检查当前文件或绑定，历史审阅与未来应用校验分别负责。

提案创建工具：`apps/api/app/tools/create_file_edit_proposal.py` 复用预览参数校验并调用保存服务，在registry.py独立注册；测试为 `apps/api/tests/tools/test_create_file_edit_proposal_tool.py` 及 `test_file_edit_proposal_integration.py`。原预览工具不变，创建仅返回pending回执。

提案PC验收：`apps/web/test/browser/proposal-tools.mjs` 驱动真实浏览器；`proposal_model.py` 提供受控决策及隔离库核对，`run-isolated.py` 仅在该脚本下触发提案数据库检查。不增加生产测试接口。

提案回执展示：`apps/web/src/features/chat/file-edit-proposal-view.ts` 校验公开协议，`components/file-edit-proposal-card.tsx` 显示待审批回执；由tool-result.tsx分派，测试为 `apps/web/test/features/chat/file-edit-proposal.test.ts`，浏览器沿用proposal-tools.mjs。无详情请求或批准/应用按钮。

提案详情HTTP：`apps/api/app/routers/workspace/workspace.py` 注册Task下file-edit-proposals GET，响应位于 `apps/api/app/schemas.py` 的FileEditProposalDetailResponse，复用授权查询服务；专项 `apps/api/tests/workspace/test_file_edit_proposal_api.py`。

提案详情BFF：`apps/web/src/features/workbench/file-edit-proposal-data.ts` 校验并投影公开详情，`src/app/api/_shared/file-edit-proposal-proxy.ts` 负责local凭证/取消/安全响应，Task下 `file-edit-proposals/[proposalId]/route.ts` 提供GET入口。专项 `apps/web/test/features/workspaces/file-edit-proposal-route.test.ts`，fetch上游受控。

提案详情按需展示：`apps/web/src/features/chat/components/file-edit-proposal-detail.tsx` 维护读取/取消/错误状态，ToolResult以Workspace/Task/Proposal三标识key挂载，ChatPanel仅在非空Task时注入范围。初始展示测试 `apps/web/test/features/chat/file-edit-proposal-detail.test.ts`，交互证据沿用proposal-tools.mjs。
