# 环境与依赖

这份文档专门说明：**需要安装什么、如何启动前后端、怎样选择解释器，以及出错时如何排查**。

## 1. 项目需要什么

- Git
- Python 3.10 或更高版本
- VS Code（推荐）
- Docker Desktop（启动 PostgreSQL + pgvector 与 Redis）
- Node.js 24 LTS（Next.js 要求至少 Node.js 20.9）
- pnpm 10.34.1（版本固定在 `apps/web/package.json`）
- DeepSeek API Key（调用 `/chat` 时需要）

`apps/api` 是 FastAPI Agent 后端，`apps/web` 是 Next.js Agent 前端。完整运行时，两者都需要启动；浏览器只访问 Next.js，Next.js 再代理请求到 FastAPI。

### 第三方依赖

| 库 | 作用 |
|---|---|
| `fastapi` | 编写 Web API |
| `pydantic` | 校验请求和响应数据 |
| `pydantic-settings` | 统一读取和校验环境配置 |
| `python-dotenv` | 从 `.env` 读取环境变量 |
| `openai` | 通过兼容接口调用 DeepSeek |
| `SQLAlchemy` + `psycopg` | 访问 PostgreSQL 数据库 |
| `alembic` | 管理数据库表结构迁移 |
| `pytest` + `httpx` | 执行自动化测试与接口测试 |
| `uvicorn` | 启动 FastAPI 应用 |
| `ruff` | 格式化和检查 Python 代码 |
| `next` + `react` | 构建 Agent 前端和 BFF 路由 |
| `tailwindcss` | 编写 Agent UI 样式 |
| `pnpm` | 锁定并安装前端依赖 |

`sqlite3`、`os`、`json` 和 `pathlib` 是 Python 标准库，不需要单独安装。

## 2. 首次安装

下面的命令只需要在第一次下载项目，或重新创建虚拟环境时执行。

### macOS / Linux

```bash
git clone https://github.com/wxc4234/ai-agent-learning-lab.git
cd ai-agent-learning-lab

python3 -m venv .venv
source .venv/bin/activate

python -m pip install -r requirements.txt
cp .env.example .env
pnpm install --dir apps/web --frozen-lockfile
```

### Windows：先安装系统工具

在新的 Windows 电脑上，先安装以下工具：

1. [Git for Windows](https://git-scm.com/download/win)。
2. [Python 3.10 x64](https://www.python.org/downloads/windows/)；安装时勾选 **Add Python to PATH**。
3. [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)；按安装向导启用 WSL 2 后端。
4. [Node.js 24 LTS](https://nodejs.org/en/download)；不要选择 Current 版。

安装完成后，重新打开 PowerShell，执行：

```powershell
git --version
py -3.10 --version
docker version
docker compose version
node --version
npm install --global pnpm@10.34.1
pnpm --version
```

`pnpm --version` 应为 `10.34.1`。项目的 `packageManager` 已固定该版本，避免不同电脑使用不同包管理器解析依赖。

### Windows PowerShell：克隆并安装项目依赖

```powershell
git clone https://github.com/wxc4234/ai-agent-learning-lab.git
cd ai-agent-learning-lab

py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install -r requirements.txt
Copy-Item .env.example .env

pnpm install --dir apps/web --frozen-lockfile
```

如果 PowerShell 不允许激活脚本，可以先对当前终端临时放行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### 已经安装 uv（可选）

`uv` 只是更快的依赖安装工具，不是本项目必须使用的工具。激活虚拟环境后，可以把安装命令换成：

```bash
uv pip install -r requirements.txt
```

如果 `python -m pip` 提示没有 pip，可以使用上面的 `uv` 命令，或者执行：

```bash
python -m ensurepip --upgrade
python -m pip install -r requirements.txt
```

### Windows 已有环境：同步本次认证字段与索引修复（2026-09-11）

本次更新包含 `argon2-cffi==25.1.0`、用户登录字段迁移 `a91c42e7d603`、会话唯一索引修复迁移 `b62d19f804ae`。代码同步不会同步两台电脑的数据库，Windows 需要在自己的 PostgreSQL 上执行升级。

**同步前提：Windows 必须拉取包含本节所列代码和迁移的远程 main，不能只复制部分源码。下方 Test-Path 用于确认迁移已同步。不要提交 `.env` 或复制 Mac 的 `.venv`。**

以下针对已有 Windows 仓库与虚拟环境。先启动 Docker Desktop，停止正在运行的 FastAPI（对应终端 Ctrl+C），在项目根目录打开 PowerShell。命令分步执行，任一步报错就停止，不要继续迁移或启动服务。

1. 检查分支和未提交修改，再同步代码：

```powershell
git status --short --branch
```

应在 `main` 且工作区干净。有本地修改先保留并处理；不要用强制 reset 覆盖学习代码。确认后执行：

```powershell
git pull --ff-only origin main
Test-Path apps\api\migrations\versions\b62d19f804ae_normalize_conversation_unique_index.py
```

`Test-Path` 必须输出 `True`。如果是 `False`，说明尚未拿到本次迁移，先解决同步问题。

2. 更新 Windows 自己的依赖并启动基础服务。直接调用解释器，无需激活脚本：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
docker compose -f infra/compose.yaml up -d
docker compose -f infra/compose.yaml ps
```

确认 PostgreSQL 和 Redis 均为 `healthy`，根目录已有本机 `.env`，`DATABASE_URL` 指向自己的学习数据库。不要用 `.env.example` 覆盖已有 `.env`。若数据重要，先做数据库备份。

3. 在后端目录检查版本并升级：

```powershell
cd apps\api
..\..\.venv\Scripts\python.exe -m alembic current
..\..\.venv\Scripts\python.exe -m alembic heads
..\..\.venv\Scripts\python.exe -m alembic upgrade head
..\..\.venv\Scripts\python.exe -m alembic current
..\..\.venv\Scripts\python.exe -m alembic check
```

本记录对应版本的 head 是 `b62d19f804ae`，升级后应显示该版本和 `(head)`；后续新增课程迁移时以 `alembic heads` 为准。结构检查预期输出 `No new upgrade operations detected.`。

- 从 `fed4e53cb0f7` 升级会依次应用两条新迁移；从 `a91c42e7d603` 只执行索引修复。
- Windows 如果原本已有正确的唯一索引，修复迁移会保留它，不重复创建。
- 空数据库可从初始迁移升级；若表已存在却没有迁移版本，停止并排查，不要盲目 `stamp head`。
- 不执行 `downgrade`、删表或 `docker compose down -v`。用户字段迁移的回退会丢失凭证字段，删除卷会丢失本机数据。
- 此修复要求在线 PostgreSQL；不要加 `--sql`，不要针对旧 `chat.db` 操作。

4. 验收后启动后端（仍在 `apps\api`）：

```powershell
..\..\.venv\Scripts\python.exe -m pytest -q
..\..\.venv\Scripts\python.exe -m ruff check app tests
```

当前普通测试预期 `202 passed, 5 skipped`。5 条 PostgreSQL 迁移测试需显式开启，在独立 schema 中执行并最终回滚，要求本机数据库账号有创建 schema 权限：

```powershell
$env:RUN_POSTGRES_MIGRATION_TESTS = "1"
try {
    ..\..\.venv\Scripts\python.exe -m pytest -q
} finally {
    Remove-Item Env:RUN_POSTGRES_MIGRATION_TESTS -ErrorAction SilentlyContinue
}
```

当前完整验收预期 `207 passed`；后续新增测试时数量会增加。以上 Windows 命令已按仓库路径整理，尚未在 Windows 实机执行，不能把 Mac 的测试结果当作 Windows 已验收。

```powershell
..\..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

前端另开终端，在仓库根目录执行 `pnpm install --dir apps/web --frozen-lockfile`，再进入 `apps/web` 执行 `pnpm dev`。

## 3. 配置 DeepSeek API Key

在项目根目录找到刚复制出来的 `.env`，填写：

```dotenv
DEEPSEEK_API_KEY=在这里填写你的key
DATABASE_URL=postgresql+psycopg://agent_app:agent_local_password@127.0.0.1:5432/agent_lab
```

注意：

- 不要把真实 Key 写进 Python 代码或文档。
- `.env` 已被 `.gitignore` 忽略，不会提交到 GitHub。
- FastAPI 的 Pydantic Settings 会主动读取根目录 `.env`。
- VS Code 提示 `python.terminal.useEnvFile` 时，可以启用它，但项目读取 Key 并不依赖这个设置。

前端的 BFF 默认代理到 `http://127.0.0.1:8000`。只有后端地址变化时，才复制 `apps/web/.env.example` 为 `apps/web/.env.local` 并修改 `API_BASE_URL`；不要使用 `NEXT_PUBLIC_` 前缀。

## 4. 启动本地基础服务

在启动 FastAPI 前，先在**项目根目录**启动 PostgreSQL + pgvector 与 Redis：

```bash
docker compose -f infra/compose.yaml up -d
docker compose -f infra/compose.yaml ps
```

两个服务的状态都应显示为 `healthy`。本地开发连接信息：

- PostgreSQL：`127.0.0.1:5432`，数据库 `agent_lab`
- Redis：`127.0.0.1:6379`

`.env` 还需要包含本地 PostgreSQL 的连接地址：

```dotenv
DATABASE_URL=postgresql+psycopg://agent_app:agent_local_password@127.0.0.1:5432/agent_lab
```

此密码仅用于本地学习环境；生产环境必须改用 Secret 管理。停止服务时使用 `docker compose -f infra/compose.yaml down`；它不会删除命名卷中的本地数据。

## 5. 在 VS Code 中选择正确解释器

1. 用 VS Code 打开整个 `ai-agent-learning-lab` 文件夹。
2. 打开命令面板，运行 `Python: Select Interpreter`。
3. 选择项目根目录 `.venv` 中的 Python：
   - macOS / Linux：`.venv/bin/python`
   - Windows：`.venv\Scripts\python.exe`

在终端中验证：

```bash
python --version
python -c "import sys; print(sys.executable)"
python -m pip show fastapi
```

解释器路径中应该包含当前项目的 `.venv`。通常同一个 VS Code 工作区只需要选择一次；新建 Python 文件不需要重新选择。

## 6. 启动项目

每次重新打开终端后，先激活虚拟环境。

macOS / Linux：

```bash
source .venv/bin/activate
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
```

然后进入接口目录：

```bash
cd apps/api
```

### 启动 AI 对话接口

首次启动或换电脑后，先执行数据库迁移：

```bash
python -m alembic upgrade head
```

再启动 FastAPI：

```bash
python -m uvicorn app.main:app --reload
```

打开 Swagger：<http://127.0.0.1:8000/docs>。

调用 `POST /chat` 的请求体示例：

```json
{
  "session_id": "demo-001",
  "prompt": "请用一句话解释 AI Agent"
}
```

### 启动 CRUD 练习接口

先在终端按 `Ctrl + C` 停止当前服务，再运行：

```bash
python -m uvicorn exercises.fastapi_crud:app --reload
```

`exercises.fastapi_crud:app` 的意思是：从 `exercises/fastapi_crud.py` 中找到名为 `app` 的 FastAPI 对象。

推荐使用 `python -m uvicorn`，这样可以明确使用当前虚拟环境中的 Python，减少调用到错误解释器的情况。

### 启动 Next.js Agent 前端

保持 FastAPI 在一个终端运行；在**第二个终端**从项目根目录执行：

```bash
cd apps/web
pnpm dev
```

打开 <http://127.0.0.1:3000>。浏览器后续只调用 `POST /api/chat/stream`；该 BFF 路由会透明转发 FastAPI 的流，DeepSeek Key 不会进入浏览器。

## 7. 常见问题

### `No module named fastapi` 或 `No module named uvicorn`

通常是虚拟环境没有激活，或者依赖装到了另一个 Python：

```bash
python -c "import sys; print(sys.executable)"
python -m pip install -r requirements.txt
python -m pip show fastapi
```

### 终端显示 Python 2.7

macOS 创建虚拟环境时要使用：

```bash
python3 -m venv .venv
source .venv/bin/activate
```

激活成功后再使用 `python`。不要用系统自带的 Python 2.7 启动项目。

### VS Code 显示“无法解析导入 fastapi”

这通常只是 VS Code 选错了解释器。重新运行 `Python: Select Interpreter`，选择项目 `.venv`，然后执行 `Developer: Reload Window`。

### Windows 显示 `pnpm` 不是命令

关闭并重新打开 PowerShell，再执行：

```powershell
npm install --global pnpm@10.34.1
pnpm --version
```

如果 `node --version` 也不存在，说明 Node.js 安装后终端尚未刷新，或安装时没有写入 PATH；重新安装 Node.js 24 LTS 并打开新终端。

### Docker Desktop 没有启动

先打开 Docker Desktop，等待状态显示 Engine running，再执行：

```powershell
docker compose -f infra/compose.yaml up -d
docker compose -f infra/compose.yaml ps
```

PostgreSQL 与 Redis 都显示 `healthy` 后再启动 FastAPI。

### `input()` 后编辑器像卡住一样

`input()` 正在等待终端输入，不是程序崩溃。请在终端输入内容并按回车。学习 FastAPI 后，数据主要通过 Swagger 或 HTTP 请求传入，不再使用 `input()`。

### `/chat` 返回 `500 Internal Server Error`

Swagger 只显示结果，真正的 Python 错误在启动服务的终端中。重点检查：

1. `.env` 是否包含正确的 `DEEPSEEK_API_KEY`。
2. DeepSeek 模型名称、账户额度和网络是否正常。
3. `database.py` 中 `INSERT INTO` 的表名和字段名，是否与 `CREATE TABLE` 完全一致。
4. 终端报错中最后几行指出了哪个文件和行号。

### 修改代码后没有生效

确认启动命令带有 `--reload`。如果仍未生效，按 `Ctrl + C` 停止服务后重新启动。

## 8. 提交前检查

提交代码前确认以下文件没有进入 Git：

- `.env`
- `.venv/`
- `*.db`
- `apps/web/.env.local`
- `apps/web/node_modules/`

它们已经写在 `.gitignore` 中。`.env.example` 可以提交，但里面只能保留占位符，不能放真实 Key。
