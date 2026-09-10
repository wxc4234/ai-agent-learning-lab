# 第 2 周复盘：后端底座与跨平台环境

## 本周结论

第 2 周已经通过整周验收。FastAPI 从单文件练习演进为可持续维护的分层应用，数据源切换到 PostgreSQL，数据库结构由 Alembic 管理，本地依赖由 Docker Compose 统一启动，并建立了不消耗真实模型额度的自动化测试基线。

## 已完成

- 使用 Pydantic Settings 集中管理 DeepSeek、数据库与 Redis 配置，非法或缺失配置会在服务启动阶段暴露。
- 拆分 routers、schemas、services、repositories、tools 和 config，明确接口、业务、数据访问与工具职责。
- 使用 SQLAlchemy 建立用户、会话、消息、Agent Run 与运行事件模型。
- 将对话持久化从 SQLite 切换到 PostgreSQL，并提供可重复执行且不会重复导入的一次性迁移脚本。
- 使用 Alembic 管理初始数据库迁移，并在空数据库验证 `upgrade head` 能创建完整业务表。
- 使用 pytest 覆盖配置、工具、仓储和主要接口错误路径，自动化测试不请求真实模型。
- 使用 Docker Compose 启动 PostgreSQL + pgvector 与 Redis，配置健康检查、Redis AOF 和命名卷持久化。
- 把 macOS 与 Windows 的安装、启动、迁移和排错命令统一记录在 `ENVIRONMENT.md`。

## 验收证据

- FastAPI 可按分层结构启动，`/chat` 和会话查询使用 PostgreSQL。
- PostgreSQL 与 Redis 健康检查通过，容器重启后 Redis 测试数据仍可读取。
- Alembic 能在空数据库创建 5 张业务表。
- 旧 SQLite 的 4 个会话、10 条消息已迁移，重复执行迁移不会重复写入。
- 测试覆盖配置、工具、仓储与接口错误契约，且不消耗模型 Token。

## 后续承接

第 3 周在这套后端底座上继续实现 Next.js 流式 Agent UI、运行事件、取消协议和冒烟评测。当前最新进度与下一课统一查看 [../../LEARNING_HANDOFF.md](../../LEARNING_HANDOFF.md)。
