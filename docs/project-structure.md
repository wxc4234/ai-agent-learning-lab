# 项目目录导航

## 按什么顺序找文件

先确定应用，再确定层级和业务领域。以任务详情为例：HTTP 入口在 `routers/workspace/tasks.py`，读取服务在 `services/tasks/task_workspace.py`，对应测试在 `tests/tasks/test_task_detail.py`。

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
            workspace/          # 根目录保留项目创建服务
                directory/      # 目录选择、绑定与授权路径
                files/          # 读取、枚举、查找、搜索及替换
                edits/          # 文本与授权文件编辑预览
                proposals/      # 提案审批、核对、执行及登记
                samples/        # 自有临时样例生命周期与门禁
                metadata/       # 文件元数据与扩展属性
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

跨目录文件查找：`apps/api/app/services/workspace/files/workspace_find.py` 在授权后进行描述符递归，固定扫描、深度、路径与结果预算；测试位于 `apps/api/tests/workspace/files/test_workspace_find.py`。工具适配已通过 `apps/api/app/tools/find_files.py` 注册为需要可信上下文的能力，专项见 `apps/api/tests/tools/test_find_files_tool.py`。

文件查找PC验收：`apps/web/test/browser/find-tools.mjs` 创建并清理临时项目，`find_model.py` 仅控制模型决策；通过既有 `run-isolated.py` 运行真实BFF/API/文件服务/隔离数据库。

文本替换预览：`apps/api/app/services/workspace/edits/workspace_edit_preview.py` 仅执行纯内存唯一替换与有界审阅Diff；测试位于 `apps/api/tests/workspace/edits/test_workspace_edit_preview.py`。授权文件读取已由 `workspace_file_preview.py` 组合，测试见 `apps/api/tests/workspace/edits/test_workspace_file_preview.py`；仍无文件写入。

文件修改预览工具：`apps/api/app/tools/preview_file_edit.py` 需要可信上下文，公开有界Diff及原文基线，不回传完整修改后文件；专项测试 `apps/api/tests/tools/test_preview_file_edit_tool.py`。此工具只读，无写入/审批能力。

修改预览PC验收：`apps/web/test/browser/preview-tools.mjs` 创建真实临时文件并逐场景核对字节不变，`preview_model.py` 仅控制模型决策，复用隔离浏览器启动器。

预览卡片：`apps/web/src/features/chat/file-edit-preview-view.ts` 校验公开协议，`components/file-edit-preview-card.tsx` 展示只读状态和Diff；组件分派由 `tool-result.tsx` 负责。测试 `file-edit-preview.test.ts` 和 `command-result.test.ts` 共用 `render-tool-result.ts` 编译真实组件，浏览器沿用preview-tools.mjs。

文件修改提案：`apps/api/app/models.py` 的 FileEditProposal 与 `migrations/versions/4eb108c473ab_add_file_edit_proposals.py` 管理待审批记录；`repositories/workspace/file_edit_proposal_repository.py` 负责归属锁和flush，`services/workspace/proposals/file_edit_proposal_service.py` 负责读文件前后的事务边界及提交。配套为 `tests/workspace/proposals/test_file_edit_proposal_service.py`、`tests/migrations/test_file_edit_proposal_migration.py`。当前不开放HTTP/工具或文件写入。

提案授权查询复用上述workspace仓库/服务文件：`read_owned_file_edit_proposal` 单条联表授权并仅选择公开列，`get_task_file_edit_proposal` 返回只读审阅快照；专项位于 `apps/api/tests/workspace/proposals/test_file_edit_proposal_query.py`。查询不检查当前文件或绑定，历史审阅与未来应用校验分别负责。

提案创建工具：`apps/api/app/tools/create_file_edit_proposal.py` 复用预览参数校验并调用保存服务，在registry.py独立注册；测试为 `apps/api/tests/tools/test_create_file_edit_proposal_tool.py` 及 `test_file_edit_proposal_integration.py`。原预览工具不变，创建仅返回pending回执。

提案PC验收：`apps/web/test/browser/proposal-tools.mjs` 驱动真实浏览器；`proposal_model.py` 提供受控决策及隔离库核对，`run-isolated.py` 仅在该脚本下触发提案数据库检查。不增加生产测试接口。

提案回执展示：`apps/web/src/features/chat/file-edit-proposal-view.ts` 校验公开协议，`components/file-edit-proposal-card.tsx` 显示待审批回执；由tool-result.tsx分派，测试为 `apps/web/test/features/chat/file-edit-proposal.test.ts`，浏览器沿用proposal-tools.mjs。无详情请求或批准/应用按钮。

提案详情HTTP：`apps/api/app/routers/workspace/proposals.py` 注册Task下file-edit-proposals GET，响应位于 `apps/api/app/schemas.py` 的FileEditProposalDetailResponse，复用授权查询服务；专项 `apps/api/tests/workspace/proposals/test_file_edit_proposal_api.py`。

提案详情BFF：`apps/web/src/features/workbench/file-edit-proposal-data.ts` 校验并投影公开详情，`src/app/api/_shared/file-edit-proposal-proxy.ts` 负责local凭证/取消/安全响应，Task下 `file-edit-proposals/[proposalId]/route.ts` 提供GET入口。专项 `apps/web/test/features/workspaces/file-edit-proposal-route.test.ts`，fetch上游受控。

提案详情按需展示：`apps/web/src/features/chat/components/file-edit-proposal-detail.tsx` 维护读取/取消/错误状态，ToolResult以Workspace/Task/Proposal三标识key挂载，ChatPanel仅在非空Task时注入范围。初始展示测试 `apps/web/test/features/chat/file-edit-proposal-detail.test.ts`，交互证据沿用proposal-tools.mjs。


提案决策服务：`apps/api/app/services/workspace/proposals/file_edit_proposal_decision.py`负责重新授权、固定锁序和pending到approved/rejected的单次转换，不读写文件。迁移`5fc219d584bc`扩展状态和截断批准约束；专项在`tests/workspace/proposals/test_file_edit_proposal_decision.py`与`tests/migrations/test_file_edit_proposal_decision_migration.py`。详情HTTP/BFF/前端支持三个查询时状态，创建工具回执仍只接受pending；审批HTTP入口已完成。`schemas.py`定义严格决策请求/回执，`routers/workspace/proposals.py`提供POST decision并映射安全业务冲突与结果未确认；专项为`tests/workspace/proposals/test_file_edit_proposal_decision_api.py`。审批BFF已完成：`features/workbench/file-edit-proposal-decision-data.ts`校验决策与回执，`app/api/_shared/file-edit-proposal-decision-proxy.ts`承接安全转发，`decision/route.ts`为同源POST入口；专项为`test/features/workspaces/file-edit-proposal-decision-route.test.ts`。审批交互已接入当前运行及历史运行详情。

审批交互：`apps/web/src/features/chat/components/file-edit-proposal-actions.tsx`维护决策、未知结果防重和卸载隔离，由`file-edit-proposal-detail.tsx`接入；渲染专项`test/features/chat/file-edit-proposal-actions.test.ts`，交互专项`test/browser/proposal-actions.mjs`。隔离启动器支持`BROWSER_ACTION_SCENARIOS`选择场景及对应数据库核对。

历史提案入口：`apps/web/src/features/workbench/components/task-run-panel.tsx`仅将成功的create_file_edit_proposal事件交给现有ToolResult，传递当前Workspace/Task范围；非法协议回退原始文本。专项`test/features/chat/historical-proposal.test.ts`；`proposal-actions.mjs`的`BROWSER_PROPOSAL_HISTORY=1`经刷新后的历史入口执行定向交互验证。

提案应用前只读核对：`apps/api/app/services/workspace/proposals/file_edit_proposal_preflight.py`复用仓库内部授权投影及受限读取；`workspace_file.py`/`workspace_path.py`可接收仅内部使用的expected_bound_root，在文件访问前拒绝绑定变化。专项`apps/api/tests/workspace/proposals/test_file_edit_proposal_preflight.py`。尚无HTTP/工具入口或文件应用。

提案应用占用：`apps/api/app/services/workspace/proposals/file_edit_proposal_application.py`负责单次领取及可信执行器的令牌匹配结果登记，复用已有归属锁顺序；迁移`60d32ae695cd`为FileEditProposal增加独立应用状态/内部令牌与约束。测试`tests/workspace/proposals/test_file_edit_proposal_application.py`和`tests/migrations/test_proposal_application_migration.py`。没有实际文件执行入口，持久占用对现有资源变更的保护已接入。

应用占用资源保护：`repositories/workspace/proposal_application_guard.py`在调用方Workspace锁内查询活动占用，`task_deletion_service.py`和`workspace_binding.py`调用；对应HTTP/BFF映射安全409，workbench-session将删除冲突标记为明确拒绝。测试`test_proposal_application_guard.py`/`test_proposal_application_guard_api.py`及三个BFF专项新增用例，浏览器task-delete.mjs按单场景筛选。

受限文件替换原语：`apps/api/app/services/workspace/files/workspace_file_replace.py`以描述符约束现有普通文本文件，使用同目录临时文件与替换结果分类；专项`apps/api/tests/workspace/files/test_workspace_file_replace.py`。仅临时样例范围，未接数据库执行器/HTTP/工具，元数据策略见`workspace_file_metadata.py`：macOS ACL及有界xattr复制与完整回读，非零flags拒绝，专项`test_workspace_file_metadata.py`；当前成功测试使用完整原生可见属性，不过滤provenance，不提供外部编辑器参与的CAS。

提案执行装配：`apps/api/app/services/workspace/proposals/file_edit_proposal_execution.py`组合领取、重新授权/核对、替换和登记；专项`apps/api/tests/workspace/proposals/test_file_edit_proposal_execution.py`。文件结果与数据库确认分离，未知结果不重试，未接HTTP/模型工具，仅临时样例。

应用状态只读查询：`apps/api/app/services/workspace/proposals/file_edit_proposal_application_query.py`与仓库`read_owned_proposal_application_status`提供授权后的四字段快照，专项`test_file_edit_proposal_application_query.py`。不持行锁、不读磁盘、不恢复或重试执行。

`apps/api/app/routers/workspace/router.py`按公共请求/标识类型、路由识别与统一安全边界、工作空间/目录、任务/运行历史、文件提案接口排列；同源上游状态GET为`/{workspace_id}/tasks/{task_id}/file-edit-proposals/{proposal_id}/application-status`，专项`test_proposal_application_status_api.py`。当前保留单模块，整理不改变依赖导出和函数逻辑。

应用状态BFF：`apps/web/src/app/api/_shared/proposal-application-status-proxy.ts`和对应`application-status/route.ts`负责同源查询；`features/workbench/proposal-application-status-data.ts`校验四字段/五状态/资源匹配。专项`test/features/workspaces/proposal-application-status-route.test.ts`，不触发执行或状态恢复。

应用状态展示：`apps/web/src/features/chat/components/proposal-application-status.tsx`由提案详情挂载，只在点击时查询，按资源key隔离、取消及卸载忽略旧响应；测试`proposal-application-status.test.ts`与浏览器`proposal-application-status.mjs`。不提供执行或恢复入口。

扩展属性只读快照：`apps/api/app/services/workspace/metadata/workspace_file_xattrs.py`接受调用方已安全打开的普通文件描述符，预算内读取可见属性，两轮核对；专项`test_workspace_file_xattrs.py`。不负责路径授权/复制/删除，也未接替换原语。

扩展属性探测：`apps/api/app/services/workspace/metadata/workspace_xattr_probe.py`只在自建临时文件报告调用接受/原本一致/最终回读三个独立证据，专项`test_workspace_xattr_probe.py`。报告不携带值或摘要，不作为真实项目写入许可。

受限xattr复制：`apps/api/app/services/workspace/metadata/workspace_xattr_copy.py`接受可信调用方的源/独占临时目标描述符及预期快照，拒绝额外目标属性，跳过已相同值并回读验证；专项`test_workspace_xattr_copy.py`。失败可能部分修改临时目标，清理由调用方负责，已接入元数据保护及内部replace，仍仅用于可信临时样例。

元数据装配专项：`apps/api/tests/workspace/metadata/test_workspace_metadata_xattrs.py`覆盖真实二进制/空属性、设置顺序、部分失败、同inode和变更检测；`metadata_support.py`只提供ACL故障代理，不过滤属性。

提案完整服务链路：`apps/api/tests/workspace/proposals/test_proposal_application_lifecycle.py`使用隔离数据库和真实临时文件，串联创建→审批→执行→查询，覆盖完整元数据、过期基线、重复执行、未批准和登记确认丢失；无HTTP执行入口。

提案执行公开契约：`apps/api/app/schemas.py`的`FileEditProposalExecutionResponse`与`apps/api/app/routers/workspace/proposal_execution_response.py`提供严格组合校验和显式公开投影；专项`apps/api/tests/workspace/proposals/test_proposal_execution_response.py`。转换函数尚未接路由，不提供授权、执行或重试。

前端执行回执解析：`apps/web/src/features/workbench/proposal-execution-data.ts`严格核对资源与回执组合，显式公开投影；专项`apps/web/test/features/workspaces/proposal-execution-data.test.ts`含真实后端Schema交叉对照，依赖仓库`.venv`。尚未接入BFF或UI。

执行回执只读组件：`apps/web/src/features/chat/components/proposal-execution-result.tsx`接收unknown回执和当前资源范围，重新校验后分列文件/登记/清理结果；专项`apps/web/test/features/chat/proposal-execution-result.test.ts`。独立组件，尚未接入工作台，没有执行、查询或重试行为。

应用请求契约：`apps/api/app/schemas.py`中的`FileEditProposalExecutionRequest`仅允许必填`action=apply`，专项`apps/api/tests/workspace/proposals/test_proposal_execution_request.py`；尚未接入路由，不承担临时样例范围识别或资源授权。

服务端临时样例：`apps/api/app/services/workspace/samples/temporary_proposal_sample.py`独占创建固定文件及目录，持有描述符核对并有限清理；专项`apps/api/tests/workspace/samples/test_temporary_proposal_sample.py`。不是资源登记或HTTP授权门禁，清理失败保留现场。

样例进程内登记：`apps/api/app/services/workspace/samples/temporary_sample_registry.py`统一创建、独占借用与关闭，活动借用延迟清理，失效后拒绝使用；专项`apps/api/tests/workspace/samples/test_temporary_sample_registry.py`。不接受任意目录注册，尚无用户任务绑定/HTTP门禁或持久化恢复。

样例用户任务绑定：`apps/api/app/services/workspace/samples/task_sample_binding.py`封装内部绑定、授权借用和先解绑后清理；专项`apps/api/tests/workspace/samples/test_task_sample_binding.py`使用隔离PostgreSQL。进程内来源登记不对外暴露，提交未知或使用异常保留封锁现场，尚未装配执行器/HTTP。

样例执行装配：`apps/api/app/services/workspace/samples/sample_proposal_execution.py`只在可信用户任务样例借用中调用执行器，贯穿期望根目录/固定相对路径及创建时目录身份；专项`apps/api/tests/workspace/samples/test_sample_proposal_execution.py`。未知/不确定回执封锁并保留现场，无HTTP入口。


## Workspace 职责导航（2026-09-22）

`services/workspace/` 根目录保留项目创建服务；其余模块按共同维护的职责分组，测试采用同名子目录。新增模块先归入已有职责，不按文件数量机械拆层。

| 子目录 | 职责 |
|---|---|
| `directory/` | 本机目录选择、绑定、规范化和授权路径解析 |
| `files/` | 文件读取、枚举、查找、搜索与受限替换 |
| `edits/` | 纯内存文本替换预览及授权文件预览 |
| `proposals/` | 提案创建、审批、核对、执行、应用登记与查询 |
| `samples/` | 服务端临时样例创建、登记、任务绑定及执行门禁 |
| `metadata/` | 元数据、扩展属性读取、探测和复制 |

`routers/workspace/router.py` 只聚合子路由。`projects.py` 管项目，`directories.py` 管目录，`tasks.py` 管任务，`proposals.py` 管提案审阅/审批/状态，`execution.py` 管受限样例应用。各子路由复用 `boundary.py` 的 WorkspaceRoute；请求分类、公共参数、安全错误分别在 `request_kinds.py`、`parameters.py`、`http.py`/`errors.py`。事务边界继续由原有业务函数负责，未新增转发壳。

受限样例应用入口为 POST `/workspaces/{workspace_id}/tasks/{task_id}/file-edit-proposals/{proposal_id}/apply`，仅接受严格 `action=apply`。登记仍由内部测试/受控调用建立，默认进程登记为空；接口不接受客户端路径，也不开放任意项目写入。

其他目录检查：Runtime 已按 agent/execution/command/sandbox/docker 分组，其内部模块职责相关，暂不再机械拆层。后续可单独整理 `apps/web/src/features/chat/components/chat-panel.tsx`（对话编排与展示）、`apps/web/src/features/workbench/workbench-session.tsx`（工作台状态与操作）和 `apps/api/app/services/chat/chat_service.py`（聊天执行编排）。这些属于独立状态/执行链路，本次只记录，未扩大结构重构范围。

提案应用BFF：`apps/web/src/app/api/_shared/proposal-execution-proxy.ts`负责严格输入、本机凭证、上游预算与安全回执；`apps/web/src/app/api/workspaces/[workspaceId]/tasks/[taskId]/file-edit-proposals/[proposalId]/apply/route.ts`为同源POST入口。复用`features/workbench/proposal-execution-data.ts`解析器，专项为`apps/web/test/features/workspaces/proposal-execution-route.test.ts`。客户端请求状态管理和页面触发仍待后续。

应用客户端请求控制器：`apps/web/src/features/workbench/proposal-execution-request.ts`，提供select/submit/cancel/getState/subscribe/dispose，页面负责注入sessionStorage与fetch。先保存标记再转发，按代次隔离迟到响应，合法回执保留证据；不接UI、不自动重试或解锁，不提供跨标签页锁。测试：`apps/web/test/features/workspaces/proposal-execution-request.test.ts`。

样例应用独立交互组件：`apps/web/src/features/chat/components/proposal-execution-actions.tsx`，按资源key隔离会话、挂载后注入浏览器存储和fetch，装配控制器与结果展示；尚未挂工作台。测试`apps/web/test/features/chat/proposal-execution-actions.test.ts`覆盖静态及受控事件，真实PC浏览器验收待下一课。

组件PC浏览器夹具：`apps/web/test/browser/execution-component/`独立编译真实组件和样式，使用StrictMode、真实sessionStorage及受控网络回执；运行产物在`apps/web/output/playwright/execution-component/`，源码不注册产品页面。受控浏览器验收已通过，真实BFF/API文件写入闭环待下一课。

真实样例应用端到端夹具：`apps/web/test/browser/execution-e2e/`，API启动内部登记自有样例，临时Next复制真实BFF/组件，Chrome真实提交，独立查询文件/数据库并验证重复保护与测试收尾。无生产测试入口；输出在`apps/web/output/playwright/execution-e2e/`。

样例登记状态：`services/workspace/samples/task_sample_binding.py`的`read_status`返回冻结TaskSampleStatus，先重新授权再投影missing/busy/sealed/ready，不读文件或恢复登记；测试`tests/workspace/samples/test_task_sample_status.py`。目前仅内部服务，HTTP待下一课。

登记查询HTTP：`routers/workspace/samples.py`提供Task下GET sample-status，公开Schema位于schemas.py；request_kinds/boundary/errors复用统一安全边界。专项`tests/workspace/samples/test_task_sample_status_api.py`；无样例创建/恢复接口，BFF待下一课。
