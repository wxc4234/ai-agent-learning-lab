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
            workspace/          # 项目、目录、任务和授权代码上下文 HTTP 接口
            runtime/            # 运行、取消和工具接口
            system/             # 健康检查
        services/
            auth/               # 本地身份及账号凭证/会话服务
            chat/               # 对话编排与消息持久化协调
            workspace/          # 根目录保留项目创建服务
                directory/      # 目录选择、绑定与授权路径
                files/          # 读取、代码分块/生成保存、向量存储/授权查询上下文串联、查找及替换
                edits/          # 文本与授权文件编辑预览
                proposals/      # 提案审批、核对、执行及登记
                samples/        # 自有临时样例生命周期与门禁
                metadata/       # 文件元数据与扩展属性
            tasks/              # 任务创建、详情、列表、标题
            model/              # 聊天、代码/查询Embedding客户端、决策适配和计价
            runtime/            # 按运行时职责继续分组
                agent/          # Agent Loop、Token 预算、工具上下文与事件
                execution/      # 会话占用、并发、线程、取消及进程恢复
                command/        # 命令契约、环境、有界输出与异步读取
                sandbox/        # 容器策略、身份和生命周期
                docker/         # Docker 客户端与 attach 协议
        repositories/
            auth/               # 用户和登录会话持久化
            chat/               # 会话与消息持久化
            workspace/          # 项目查询/归属、提案和受控向量批次持久化
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

代码上下文BFF：`apps/web/src/app/api/workspaces/[workspaceId]/tasks/[taskId]/code-query-context/route.ts` → `_shared/code-query-context-proxy.ts` / `code-query-context-json.ts` → `features/workbench/code-query-context-data.ts`。前两项处理服务端转发与有界读取，features模块只处理公开数据；专项位于`apps/web/test/features/workspaces/code-query-context-{data,route}.test.ts`，协议见[同源BFF](code-context.md#同源-bff-代理)。

代码向量批次摘要：`apps/api/app/routers/workspace/code_batches.py` → `services/workspace/files/code_batch_summaries.py` → `repositories/workspace/code_embedding_repository.py::read_owned_code_embedding_batch_summaries`。专项位于`apps/api/tests/workspace/files/test_code_batch_summaries{,_validation}.py`，范围与公开契约见[摘要API](code-vector-storage.md#授权批次摘要-api)。

内部代码生成与保存：`services/workspace/files/code_batch_generation.py` → `python_chunks.py` / `services/model/code_embeddings.py` → `code_vector_storage.py`。路径/读取/枚举服务传递捕获绑定修订，模型每次请求前复核目标；专项为`apps/api/tests/workspace/files/test_code_batch_generation.py`，范围见[组合协议](code-vector-storage.md#授权代码生成与保存串联)。没有HTTP或产品入口。

批次摘要BFF：`apps/web/src/app/api/workspaces/[workspaceId]/tasks/[taskId]/code-embedding-batches/route.ts` → `_shared/code-batch-summaries-proxy.ts` → `features/workbench/code-batch-summaries-data.ts`，复用上述`code-query-context-json.ts`读取器。专项位于`apps/web/test/features/workspaces/code-batch-summaries-{data,route}.test.ts`，协议见[摘要BFF](code-vector-storage.md#批次摘要同源-bff)。

当前产品工作台：`features/chat/components/chat-panel.tsx`只挂载对话和按需`task-changes-panel.tsx`；`workbench-shell.tsx`仅提供“查看改动”，`workbench-session.tsx`不再有advanced详情状态。内部批次/上下文实验组件没有产品引用，公开数据校验/API/BFF保留；产品验收为`test/browser/workbench-product/run.mjs`，范围见[UI协议](agent-ui-events.md#当前产品界面验收)。
