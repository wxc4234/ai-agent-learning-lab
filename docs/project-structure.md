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
            runtime/            # Agent Loop、取消、预算和工具事件
        repositories/
            auth/               # 用户和登录会话持久化
            chat/               # 会话与消息持久化
            workspace/          # 项目查询与归属检查
            runtime/            # 运行/事件持久化
        tools/
            registry.py         # 当前工具注册入口
    tests/
        conftest.py             # 所有领域共享的隔离 PostgreSQL 夹具
        auth/
        chat/
        core/                   # 配置、健康检查、数据库隔离
        local/
        migrations/             # Alembic 兼容性验证
        model/
        runtime/
        tasks/
        tools/
        workspace/
    migrations/
        versions/               # 保留 Alembic 按修订顺序管理的脚本
```

分层职责不变：路由管理 HTTP 边界，服务编排业务，仓储负责数据访问。业务分组用于导航，不要求每个领域都机械建立所有层，也不为一个辅助函数再加一层文件夹。

`models.py`、`schemas.py` 和运行配置仍是明确的公共入口，本轮不为目录美观拆散 ORM 注册与协议类型；后续确有独立演进需要再按领域拆分。前端已经采用 `src/features/` 领域结构与 Next.js `app/` 路由结构，继续沿用。

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
