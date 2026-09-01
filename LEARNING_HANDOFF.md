# AI Agent 学习交接

更新时间：2026-09-01（Asia/Shanghai）

这份文件只记录**当前进度、恢复方式和下一课**。完整路线统一查看 [LEARNING_PLAN.md](LEARNING_PLAN.md)，逐日任务与验收标准查看 [LEARNING_CURRICULUM.md](LEARNING_CURRICULUM.md)。

目标岗位：**AI Agent 应用开发工程师（偏前端 / 全栈产品工程）**。当前学习后端是为了能够独立交付 Agent 产品，不是转向纯后端或泛化的大模型应用岗位。

## 目录规则（长期有效）

`week-XX/` 只保存单周记录和一次性练习。任何会被后续周次继续开发的源码、配置、测试、环境说明或基础设施，都不得放入某个 `week-XX/` 目录。当前跨周 FastAPI 后端统一位于 `apps/api/`，后续 Next.js 前端放入 `apps/web/`。详细规则见 [AGENTS.md](AGENTS.md)。

## 当前进度

第 1 周与第 2 周已完成。下一课是第 3 周 Day 1：**初始化 Next.js Agent UI，并理解 AG-UI 的事件模型。**

当前代码已经具备：

- FastAPI + DeepSeek 对话接口。
- `session_id` 会话隔离和最近 5 轮上下文。
- Docker Compose 启动 PostgreSQL + pgvector 与 Redis，两个服务的健康检查通过。
- Redis 已验证读写；开启 AOF 和命名卷后重启容器，测试数据仍可读取。
- SQLAlchemy 数据模型：`users`、`conversations`、`messages`、`agent_runs`、`agent_run_events`。
- 已将旧 SQLite 的 4 个会话、10 条消息迁移到 PostgreSQL，并验证重复执行不会重复导入。
- `/chat` 与会话查询接口已切换为 PostgreSQL 持久化。
- Alembic 已初始化并接入 SQLAlchemy metadata；初始迁移为 `fed4e53cb0f7_create_agent_schema.py`。
- 当前 `agent_lab` 已标记到该迁移版本；并已在空数据库执行 `alembic upgrade head`，验证可创建 5 张业务表。
- pytest 测试套件已建立并通过：覆盖时间工具、工具注册白名单、用户仓储、健康检查及聊天接口的 422/502 错误契约；测试不调用模型 API。
- 无参数时间工具 `get_current_time`。
- 一次完整的 Tool Calling 执行闭环。

尚未完成：带参数工具、通用工具调度、多工具循环。这些内容不会删除，统一放到第 7 周 Agent Runtime 阶段完成。

## 当前文件

| 文件 | 作用 |
|---|---|
| `apps/api/app/main.py` | FastAPI 应用入口，注册路由并初始化数据库 |
| `apps/api/app/database.py` | SQLAlchemy Engine、Session 和 ORM 基类 |
| `apps/api/app/models.py` | 用户、会话、消息、Agent Run 与事件模型 |
| `apps/api/app/repositories/` | PostgreSQL 用户、会话与消息数据访问层 |
| `apps/api/migrations/` | Alembic 表结构迁移历史 |
| `apps/api/alembic.ini` | Alembic 配置入口 |
| `apps/api/tests/` | pytest 自动化测试：工具、仓储与接口契约 |
| `apps/api/scripts/migrate_sqlite_to_postgres.py` | 一次性 SQLite 历史消息迁移脚本 |
| `docs/architecture.md` | 当前服务职责与请求、数据流向图 |
| `infra/compose.yaml` | 跨平台 PostgreSQL + pgvector、Redis 本地服务 |
| `ENVIRONMENT.md` | 安装、启动和常见问题 |

## 当前接口

| 接口 | 作用 |
|---|---|
| `GET /` | 服务健康检查 |
| `GET /chat` | 提示使用 POST |
| `POST /chat` | 带 PostgreSQL 记忆的 DeepSeek 对话 |
| `GET /sessions/{session_id}/messages` | 查询 PostgreSQL 会话历史 |
| `POST /tool-test` | 测试时间工具调用 |

## 下一课

第 3 周 Day 1：**Next.js Agent UI 初始化。**

本课先完成一个小目标：确认 Node.js 与 pnpm 环境，并在 `apps/web/` 初始化 TypeScript + Tailwind 的 Next.js 应用。学习顺序：

1. Next.js App Router 与当前 FastAPI 后端的职责边界。
2. 初始化不含业务逻辑的前端应用。
3. 阅读 AG-UI 事件类型，比较它与本项目后续事件设计。

验收标准：

- `pnpm dev` 可启动本地前端。
- 前端源码位于跨周目录 `apps/web/`。
- 能说出前端需要展示的最小 Agent 运行状态。

## 换电脑后恢复

Windows 首次使用：

```powershell
git clone https://github.com/wxc4234/ai-agent-learning-lab.git
cd ai-agent-learning-lab
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

已有仓库时：

```powershell
git pull origin main
.\.venv\Scripts\Activate.ps1
```

填写本机 `.env` 后启动：

```powershell
cd apps\api
python -m uvicorn app.main:app --reload
```

打开 <http://127.0.0.1:8000/docs>。不要复制其他操作系统生成的 `.venv`。

## 每次学习结束只更新这里

后续只需要维护三处内容：

- 当前进度。
- 下一课和验收标准。
- 新增的重要文件或启动变化。

不要再把完整 12 周路线复制到本文件。
