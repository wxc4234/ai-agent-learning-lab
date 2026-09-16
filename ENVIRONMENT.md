# 环境与依赖

> 当前主运行方式（2026-09-15）：本地优先、免产品登录。完成下方依赖安装及迁移后，在仓库根目录运行 `.venv/bin/python scripts/run_local.py`；Windows 为 `.venv\Scripts\python.exe scripts\run_local.py`。旧认证章节保留为历史/账号模式说明。详见 [本地模式说明](docs/local-runtime.md)。


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

下面的命令只需要在第一次下载项目，或重新创建虚拟环境时执行。当前统一建议 Python 3.12；现有代码使用 `datetime.UTC`，不能使用 Python 3.10。已有虚拟环境不会随系统 Python 自动升级，运行前先检查 `python --version`。

### macOS / Linux

```bash
git clone https://github.com/wxc4234/ai-agent-learning-lab.git
cd ai-agent-learning-lab

python3.12 -m venv .venv
source .venv/bin/activate

python -m pip install -r requirements.txt
cp .env.example .env
pnpm install --dir apps/web --frozen-lockfile
```

### Windows：先安装系统工具

在新的 Windows 电脑上，先安装以下工具：

1. [Git for Windows](https://git-scm.com/download/win)。
2. [Python 3.12 x64](https://www.python.org/downloads/windows/)；安装时勾选 **Add Python to PATH**。
3. [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)；按安装向导启用 WSL 2 后端。
4. [Node.js 24 LTS](https://nodejs.org/en/download)；不要选择 Current 版。

安装完成后，重新打开 PowerShell，执行：

```powershell
git --version
py -3.12 --version
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

py -3.12 -m venv .venv
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

2026-09-13 Windows 验证：后端 `335 passed`，Ruff 通过（含中文用户名、登录凭证服务、21 条注册 HTTP 接口测试及 29 条登录会话仓储/迁移测试）。数据库测试统一使用 PostgreSQL + psycopg，迁移与注册测试默认执行，不再需要 `RUN_POSTGRES_MIGRATION_TESTS` 或 `RUN_POSTGRES_REGISTRATION_TESTS` 开关，也不回退 SQLite。

登录会话课的最新迁移为 `c83f20a915bd`，父版本为 `b62d19f804ae`；沿用上面的 `alembic upgrade head` 与 `alembic check` 命令。该迁移新增 `login_sessions`，不重建已有业务表。本机已升级至新 head，结构检查通过，原五张业务表升级前后逐行一致；其他电脑仍需各自执行升级。

`tests/conftest.py` 从本机 `DATABASE_URL` 取得服务器与账号信息，连接 `postgres` 维护库，每轮自动创建一个随机命名的 `agent_lab_test_<uuid>` 独立数据库。每个测试使用独立 schema，允许真实 commit/rollback；测试结束后自动删除本次创建的 schema 和数据库，不复用、清空或删除开发库，不生成 `.test-tmp-*` 目录。

账号需要连接维护库及 `CREATEDB` 权限；本项目本地 Compose 的初始化账号可用于这一流程。其他环境可通过环境变量 `TEST_DATABASE_ADMIN_URL` 指定测试服务器的 `postgresql+psycopg` 管理连接，勿将含密码的 URL 提交到仓库。该变量只用于测试配置，不改变应用 `DATABASE_URL`。权限或连接不满足时测试会报错，不静默跳过。

正常失败也会执行清理；若进程被强制终止，可能残留随机测试库。清理时先核实具体库名和用途，仅删除已确认属于中断测试的库，不批量删除其他数据库。纯逻辑测试未请求数据库 fixture 时，不会创建测试库。

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
python3.12 -m venv .venv
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

## 2026-09-14 项目 Python 环境升级与签发验收

项目 `.venv` 已由 Python 3.10 升级为 Python 3.12.13，并按根目录 `requirements.txt` 安装依赖和补齐 pip。根目录 `.python-version` 固定使用 3.12，供 uv 等工具选择解释器；它不会改变系统全局 Python 或已经启动的终端进程。

旧环境保存在 `venv/python310-backup-20260914/`（已被 Git 忽略）。该备份仅供回退；虚拟环境中的脚本包含绝对路径，不能直接在备份路径激活使用。需要回退时，先将当前 `.venv` 另行保存，再把旧目录移回 `.venv`。

已有终端应重新激活环境，正在运行的后端或编辑器 Python 进程需重启。VS Code 继续选择项目 `.venv/bin/python`，不选择旧的 `python3.10` 或临时验收环境。

在仓库根目录执行：

```bash
source .venv/bin/activate
python --version
python -m pip check
cd apps/api
python -m pytest -q
python -m ruff check app tests
python -m uvicorn app.main:app --reload
```

预期 Python 版本为 3.12.13。数据库测试使用 conftest 的独立 PostgreSQL 库和私有 schema，本次没有开发库迁移。签发服务包含 16 条测试，完整后端回归 351 passed，Ruff 和 pip 依赖一致性检查通过；完整回归有 1 条 Starlette 引用 AnyIO BlockingPortal 的弃用警告。

## 登录 HTTP 课程验收

本机 `.env` 已设置 `LOGIN_COOKIE_SECURE=false` 供 HTTP 调试，其他配置保留。`.env.example` 已提供 Cookie 与来源配置。生产 HTTPS 必须设为 true，来源列表替换为实际前端 Origin；配置变更后重启后端。

在 `apps/api` 使用项目 Python 环境运行本课测试：

```bash
../../.venv/bin/python -m pytest -q tests/auth/test_login_api.py
```

2026-09-14 修正后本课 29 passed，项目 `.venv` 完整后端回归 380 passed，Ruff 通过；有 1 条既有 Starlette/AnyIO 弃用警告。输入合法性检查返回安全 422，不泄露原始密码。登录请求须包含允许的 Origin 和 application/json；目前仅验收后端，浏览器 BFF 登录链路尚未接入。

## 登录会话解析服务验收

在 `apps/api` 使用项目环境运行：

```bash
../../.venv/bin/python -m pytest -q tests/auth/test_login_session_resolver.py
../../.venv/bin/python -m pytest -q
../../.venv/bin/python -m ruff check app tests
```

2026-09-14 本课新增 29 条测试通过，完整后端回归 409 passed，Ruff 通过；仍有 1 条既有 Starlette/AnyIO 弃用警告。数据库测试复用独立库与私有 schema，未修改开发数据或迁移。解析服务已由后续 GET /auth/me 接入。

## 当前用户 HTTP 接口验收

在 `apps/api` 使用项目环境执行：

```bash
../../.venv/bin/python -m pytest -q tests/auth/test_current_user_api.py
../../.venv/bin/python -m pytest -q
../../.venv/bin/python -m ruff check app tests
```

2026-09-14 本课 16 passed，完整后端回归 425 passed，Ruff 通过，1 条既有 Starlette/AnyIO 弃用警告。GET /auth/me 需要登录签发的 Cookie，不需要 JSON 请求体或 Origin；无效登录返回 401。本轮只验证独立 PostgreSQL 测试库中的后端 HTTP 链路，未做浏览器/BFF 接入。

## AnyIO / Starlette 弃用警告修复（2026-09-14）

Starlette 1.6.0 的 TestClient 使用 anyio.abc.BlockingPortal 别名，AnyIO 4.15.1 对该别名发出 DeprecationWarning。查询 PyPI 时 Starlette 最新正式版仍为 1.6.0，故 requirements 固定 AnyIO 4.14.2；该版本满足当前 Starlette、OpenAI 和 HTTP 客户端的声明约束。项目 `.venv` 已同步，安装日志只变更 AnyIO 4.15.1 → 4.14.2。

这是依赖兼容性固定，没有过滤警告、monkeypatch 或修改 site-packages 源码。上游 Starlette 发布使用 anyio.from_thread.BlockingPortal 的修复版后，再评估解除此固定。

其他电脑在根目录安装 `requirements.txt` 后，从 `apps/api` 运行严格验证：

```bash
python -W error::DeprecationWarning -m pytest -q
```

本机结果：425 passed，零警告；pip check、Ruff 均通过。上文历史课程记录中的 1 条警告已解决。已启动的后端/编辑器进程须重启以加载新依赖。

上游参考：[Starlette 发布记录](https://www.starlette.io/release-notes/)、[AnyIO 版本历史](https://anyio.readthedocs.io/en/latest/versionhistory.html)。

## 登出服务验收（2026-09-14）

在 apps/api 使用项目环境：

```bash
../../.venv/bin/python -W error::DeprecationWarning -m pytest -q tests/auth/test_logout_service.py
../../.venv/bin/python -W error::DeprecationWarning -m pytest -q
../../.venv/bin/python -m ruff check app tests
```

本课 26 passed，完整后端 451 passed、零警告，Ruff 通过。数据库测试使用独立 PostgreSQL 测试库自动清理；未修改开发数据。撤销服务已由后续 POST /auth/logout 接入并清 Cookie。

## 登出 HTTP 验收（2026-09-14）

在 apps/api 使用项目环境：

```bash
../../.venv/bin/python -W error::DeprecationWarning -m pytest -q tests/auth/test_logout_api.py
../../.venv/bin/python -W error::DeprecationWarning -m pytest -q
../../.venv/bin/python -m ruff check app tests
```

本课 18 passed，完整后端 469 passed、零警告，Ruff 通过。POST /auth/logout 要求允许的 Origin，无请求体要求；成功 204 空正文并清 Cookie。来源拒绝或服务故障不清 Cookie。本轮仅验证隔离 PostgreSQL 中的后端 HTTP 链路，BFF/浏览器接入尚未实现。

## 登录 BFF 验收（2026-09-14）

在 apps/web 运行：

```bash
pnpm test:auth
pnpm test:state
pnpm typecheck
pnpm lint
```

认证 28/28、原有前端 71/71，TypeScript/ESLint 通过。package.json 明确 type=module，Node 测试不再产生模块类型推断警告。Next 类型生成仍提示本机 Node 通过 Rosetta 运行，属于性能提示，不影响类型检查通过。

apps/web/.env.example 提供逗号分隔的 AUTH_ALLOWED_ORIGINS，与后端 JSON 数组 LOGIN_ALLOWED_ORIGINS 取值配套；默认本地来源无需新增配置。Next 仅识别 route.ts，不要在编辑器旧标签中重新保存出 routes.ts。

本轮另用临时 Next.js（13000）、FastAPI（18000）、conftest 的随机独立 PostgreSQL 数据库和私有 schema、Playwright 无头 Chrome 完成实测：登录 Cookie 保存、HttpOnly/Lax/host/path/expiry、刷新后保持、令牌对应身份、错误密码 401、错误 Origin 403。临时 Next 使用仓库登录路由副本与同版本依赖；没有写开发业务表，服务与数据库已清理。本课没有登录页，当前用户/登出 BFF 尚未接入。


### 当前用户 BFF 验收（2026-09-14）

新增 GET /api/auth/me，只转发唯一且符合令牌格式的 agent_session；所有响应 no-store，不续期、不删除 Cookie。新增 35 条测试，复用现有 test:auth 命令。

```bash
pnpm --dir apps/web test:auth
pnpm --dir apps/web test:state
pnpm --dir apps/web typecheck
pnpm --dir apps/web lint
```

本轮结果：认证 63/63，聊天 71/71，TypeScript/ESLint 通过；后端 469 passed、零弃用警告，Ruff 通过。Next 类型生成仍提示 Rosetta 性能信息。隔离 PostgreSQL + 临时 Next/FastAPI + Playwright Chrome 已验证登录、刷新后同源身份查询、撤销重放/数据库过期/缺失 Cookie 401，查询不改变 Cookie。测试服务及数据库已自动清理，未写开发业务表。


### 登出 BFF 验收（2026-09-14）

新增 POST /api/auth/logout，沿用 test:auth / test:state / typecheck / lint 命令。新增 39 条认证测试，认证共 102/102、聊天 71/71，TypeScript/ESLint 通过；后端 469 passed、零弃用警告，Ruff 通过。核心实现无需修改。

隔离 PostgreSQL + 临时 Next/FastAPI + Playwright Chrome 验证完整同源登录、身份查询、登出、登出后 401；Cookie 实际删除、旧令牌重放无效、另一会话仍有效、重复与过期会话登出 204、拒绝来源不破坏会话。临时服务和数据库已清理，未写开发业务表。后端故障/超时/取消时不清 Cookie 由自动化测试覆盖。

### 登录页浏览器测试

测试文件：`apps/web/test/browser/login-page.mjs`；隔离启动器：`apps/web/test/browser/run-isolated.py`。启动器复用后端 conftest 的随机测试库/私有 schema，创建测试账号，复制当前三个认证 BFF、登录页和样式到临时 Next 应用，启动当前 FastAPI。测试结束自动清理服务与数据库。临时根布局不加载 Google 字体，首页只有测试导航；不是对完整聊天首页的验收。

从仓库根目录、已激活项目 `.venv` 的终端运行（需要 Node、PostgreSQL、Playwright 和 Chrome）：

```bash
pnpm --dir apps/web test:auth:page
```

该命令通过当前 Python 启动隔离测试。Playwright 默认从 Node 模块 `playwright` 加载；若使用外部已有安装，设置 `PLAYWRIGHT_MODULE` 为该模块绝对路径。可设置 `CHROME_EXECUTABLE` 为本机 Chrome 可执行文件；不设置则使用 Playwright 自带 Chromium。启动器当前面向项目 macOS/Linux `.venv/bin/python` 环境，固定使用 13000/18000 端口，需保证空闲。不要把测试脚本直接指向开发服务；用户名和密码只用于隔离库。`AUTH_TEST_FILTER` 可按场景名称筛选，完整验收时不要设置。

浏览器测试既包含真实登录/查询/登出，也包含页面层模拟 500、断网、非法身份、422、超时、重复提交与旧请求晚到。模拟结果不作为后端对应故障已复现的证据。

本机已有运行时的实际调用（换电脑需要调整这两个路径）：

```bash
PLAYWRIGHT_MODULE=/Users/wanxiancheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
.venv/bin/python apps/web/test/browser/run-isolated.py
```

2026-09-14 完整验收：10 个浏览器场景通过；认证 102/102、聊天 71/71，TypeScript/ESLint 通过，隔离启动器 Ruff 通过。用户页面核心未改动，SubmitEvent 正确。首轮取消用例对 Effect 清理断言过早，修正为等待取消，并关闭超时信号排除假阳性后，全量复跑通过。临时资源已清理。本课未重跑完整后端，469 条是上一课基线。

### UI 组件配套整理（2026-09-14）

已按用户授权引入 shadcn/ui 官方 new-york-v4 源码：Button、Input、Textarea、Label、Card，位于 `apps/web/src/components/ui/`；依赖 cn、class-variance-authority、radix-ui，锁文件一并更新。`components.json` 记录组件目录，许可证见 `apps/web/THIRD_PARTY_NOTICES.md`。共享 `globals.css` 负责语义颜色、圆角和随系统切换的明暗主题。

登录页、聊天输入区和运行摘要使用这些组件。认证请求与状态、聊天流协议、停止/重试行为保持原实现。后续课程继续原计划，不单独安排 UI 迁移课。

浏览器隔离启动器现复制共享组件和聊天组件，测试首页渲染真实 ChatPanel 并保留测试导航；聊天故障通过拦截请求模拟，不调用真实模型。新增明暗主题、移动布局、组件标记与发送错误反馈场景，已有认证场景继续复用。

本次 UI 验收：认证 102/102、聊天 71/71、浏览器 11/11 场景通过，TypeScript/ESLint 与隔离启动器 Ruff 通过，明暗主题截图已查看。摘要测试适配真实共享 Card；浏览器错误断言限定 main，排除 Next 路由播报节点。测试库和临时服务已清理，未调用模型。后端未改动、未重复执行后端全量测试。


### 首页登录态门禁验收（2026-09-14）

AuthGate 只在身份确认后渲染 ChatPanel，401 跳 /login?next=%2F，服务故障留当前页重试。登录页只接受唯一 next=/，使用 replace 返回固定首页；直接 /login 仍支持账号与退出。核心实现与参考一致，无需修改。

复用 test:auth、test:state、typecheck、lint、test:auth:page 命令。隔离启动器现在复制实际 src/app/page.tsx 为 HomePage，测试导航外层不替代门禁。新增 8 个浏览器场景，总计 19/19 通过；认证 102/102、聊天 71/71，TypeScript/ESLint 和启动器 Ruff 通过。覆盖真实登录返回/刷新/退出后再进入、三类故障、加载/重试、超时/卸载取消与晚到响应、外部/脚本/重复/重复编码返回参数。测试库与服务已清理，聊天模型未调用。本课未修改或重跑后端全量，469 条为历史基线。

页面门禁不保护直接 API 请求；聊天后端身份认证和所有权校验、页面停留期间的会话过期处理仍待后续。


### 当前用户依赖验收（2026-09-14）

新增 app/dependencies.py，GET /auth/me 通过 CurrentUser 依赖取得身份；接口响应契约保持一致。学习者核心实现与参考一致，未代改。测试中 SessionLocal 和解析函数的替换位置迁至 app.dependencies，避免误连开发库。

```bash
cd apps/api
../../.venv/bin/python -W error::DeprecationWarning -m pytest -q
../../.venv/bin/python -m ruff check app tests
```

本轮新增依赖专项 9 条，完整后端 478 passed、零警告，Ruff 通过，前端 ESLint 通过。新测试复用 conftest 独立 PostgreSQL/私有 schema；验证同步依赖线程、接口/流式正文执行前 Session 关闭、请求内缓存与跨请求隔离、错误分流和不执行接口。测试资源由 fixture 清理；没有运行模型。前端核心与协议未改动，本轮未重跑前端单测或浏览器，不把历史结果记为新验收。

### 2026-09-14 聊天认证课首次验收（待修正）

新增 `apps/api/tests/chat/test_chat_auth_boundary.py`（模型与 run 创建模拟，不访问数据库）：从 apps/api 执行 `../../.venv/bin/python -W error::DeprecationWarning -m pytest -q tests/chat/test_chat_auth_boundary.py`，结果 11 passed、3 failed。流式装饰器遗漏 require_current_user，导致拒绝认证和认证故障场景仍返回 200，成功场景亦没有执行依赖。已向学习者提供修正段，本课尚未通过。

新增聊天 BFF 34 条测试已通过（test:auth 自动纳入）；test:state 共 76 条通过，pnpm typecheck、pnpm lint、后端 Ruff 通过。本轮未跑后端全量或浏览器；真实隔离数据库认证测试、旧断言适配与浏览器链路留待核心修正后完成。

### 2026-09-14 聊天认证课修正后整体验收

学习者补齐流式路由 Depends 后，本课通过。普通 /chat 与 /chat/stream 均在创建 run/模型调用前认证；后端 ChatRoute、BFF Cookie 转发与前端 401 提示完成。认证不等于所有权，会话仍使用匿名历史归属和 session_id 缓存键，资源隔离留到下一课。

- 后端：从 apps/api 执行 `../../.venv/bin/python -W error::DeprecationWarning -m pytest -q`，**510 passed，零警告**。新增 test_chat_auth_boundary.py 14 条与 test_chat_auth_sessions.py 18 条；后者使用现有 PostgreSQL 隔离夹具，验证有效/缺失/格式错误/未知/过期/撤销会话、SQL 故障、Session 提前关闭与输入脱敏，模型/run 创建模拟。旧测试补来源和认证前提，调整聊天错误契约断言。
- 前端：`pnpm test:auth` **136 passed**，`pnpm test:state` **76 passed**；`pnpm typecheck`、`pnpm lint` 与后端/浏览器 Python 配套 Ruff 通过。Rosetta Node 性能提示仍存在，不是 Python 弃用警告。
- 浏览器：原隔离命令运行 **20/20 场景通过**，包括新增真实登录→聊天 BFF→FastAPI NDJSON→撤销→重放旧 Cookie→401/保留输入。run-isolated.py 复制真实聊天 BFF，使用 chat_test_app.py 作为测试服务器入口，要求随机测试数据库前缀。只模拟模型决策与 Redis 等待，认证、Agent Loop、聊天服务、运行与消息持久化使用真实代码；未访问真实模型。整套浏览器超时上限为 660 秒。
- 临时 Next/FastAPI 服务和随机 PostgreSQL 数据库/schema 已自动清理，开发业务表未参与测试。学习交接、重要面试题和索引已同步。

### 会话仓储所有权原语验收

新增 require_owned_conversation / get_or_create_owned_conversation 已通过，现有聊天调用尚未切换。新增 `apps/api/tests/chat/test_owned_conversation_repository.py` 15 条测试，复用 PostgreSQL 独立库/私有 schema；两个物理连接通过 pg_blocking_pids 确认锁等待，覆盖同/不同用户争用及首事务提交/回滚。测试按当前 READ COMMITTED 运行，不代表已实现 SERIALIZABLE 重试。

从 apps/api 执行 `../../.venv/bin/python -W error::DeprecationWarning -m pytest -q`：**525 passed，零警告**。Ruff 与前端 `pnpm lint` 通过。本课未改接口/UI，未重跑浏览器和前端单测；136/76/20 为上一课验收结果。核心无需修正，只补空行与文件末尾换行；测试验证所有权、重复复用、跨连接事务可见性、外层回滚与数据库异常分类。没有新增迁移或操作开发业务数据。

### 聊天读写、历史与缓存所有权接入验收

本课通过。身份从 CurrentUser.id 进入模型上下文、会话读写和 run 创建；缓存按 (user_id, session_id) 定位并在命中前查所有权。历史查询同步认证，自己的空会话 200，未知/他人 404。旧匿名创建函数和未使用导入清理，保留历史数据。

- 全量后端 `../../.venv/bin/python -W error::DeprecationWarning -m pytest -q`（apps/api）**537 passed，零警告**。新增 test_chat_ownership.py 12 条，真实隔离 PostgreSQL/令牌，只模拟模型与 Redis 等待。旧模型测试、run 测试及保存参数断言已适配 user_id。
- 前端 `pnpm test:auth` **137**、`pnpm test:state` **77**、`pnpm typecheck`、`pnpm lint` 通过；后端和浏览器 Python 配套 Ruff 通过。
- 用户发现静态类型错误后，历史路由改为逐条 ConversationMessage.model_validate，再构造 ConversationHistoryResponse；`npx --yes --package pyright pyright --pythonpath ../../.venv/bin/python app/routers/chat/conversation.py`（apps/api）**0 errors/0 warnings**。该修正后再次运行 test_chat_ownership.py，12 passed；未宣称全仓库 Pyright 已通过。npx 未改项目依赖。
- 浏览器隔离入口新增第二账号、BFF 双用户访问场景，全量 **21/21** 通过：甲创建会话、切换乙重放甲标识 404/保留输入，乙发送新会话成功。原登录、门禁、撤销重放等场景也通过。启动器超时上限 720 秒；临时服务、数据库/schema 自动清理，不调用真实模型、不操作开发业务表。
- 下一课保护运行时间线与取消入口。本课未解决同用户同会话并发顺序或普通聊天非模型失败时的缓存恢复问题。

### 2026-09-15：运行时间线与取消所有权验收

学习者明确完成后，教练检查核心实现，仅清理未使用的 RunTimeline 导入、缩进/空白与文件末尾换行；没有代写或改变本课业务逻辑。新增 RunRoute 覆盖依赖解析和执行边界，GET/POST 根据 CurrentUser.id 与 Conversation.user_id 授权；未知/他人统一 404，终态判断位于授权之后。取消与正常结束使用同一 AgentRun 行锁，事务提交后才发布通知。

验证结果：

- 后端全量 **573 passed，零弃用警告**。新增 `apps/api/tests/runtime/test_run_ownership.py` **36 条**，复用独立 PostgreSQL 数据库/私有 schema，验证真实令牌、双用户、旧匿名、幂等终态、真实行锁等待、双取消竞争、事件插入失败回滚、身份/业务数据库故障与 Redis 发布失败。旧运行仓储与 API 测试已适配身份参数。
- 前端认证/BFF **171 passed**（新增取消 BFF 34 条）；聊天状态 **84 passed**（新增取消函数行为 7 条，现有 AST 夹具补齐 requestVersionRef 与提示 setter）。测试执行生产取消函数，覆盖 204/401/404/503、独立 AbortSignal、超时、重复取消与晚到响应。
- Ruff、TypeScript、ESLint、git diff --check 通过。本课未重复 Pyright；此前历史路由 Pyright 结果仍仅是上一课证据。
- 浏览器新增取消专项 **7/7 场景通过**：本人停止和重复取消、真实撤销后 401、切换账号后 404、模拟 503/网络中断/取消请求超时、旧取消失败晚到不污染新一轮。原有 21 场景本课未重复，不能把它们算入本次运行数量。
- 浏览器复制真实取消 BFF 与页面，认证、Agent Loop、工具执行和 PostgreSQL 持久化使用真实代码；模型以固定 ToolAction/FinalAnswer 模拟，工具决策包含 Token usage，通知通过进程内 Future 模拟 Redis。仅用于隔离测试服务器，不作为真实 Redis 跨实例验证。测试结束确认临时服务、schema 与数据库自动清理，未调用真实模型或访问开发业务表。

运行方式：

```bash
# 从 apps/api
../../.venv/bin/python -W error::DeprecationWarning -m pytest -q
../../.venv/bin/python -m ruff check app tests

# 从仓库根目录
pnpm --dir apps/web test:auth
pnpm --dir apps/web test:state
pnpm --dir apps/web typecheck
pnpm --dir apps/web lint

PLAYWRIGHT_MODULE=/Users/wanxiancheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
AUTH_TEST_FILTER='run cancel' \
.venv/bin/python apps/web/test/browser/run-isolated.py
```

首轮后端因沙箱拒绝本机 TCP 连接而未进入业务断言；放行隔离测试后通过。浏览器测试编写阶段修正了按钮名称、模拟工具决策缺少 Token usage，以及误计入 Next 全局 alert 的测试断言；最终 7 场景完整重跑通过。取消时仍观察到 ASGI callable returned without completing response / Next failed to pipe response 传输关闭日志，页面和数据库断言通过；尚未排查有序关闭，不宣称该现象已解决。

一致性边界：Redis 发布失败返回 503，但数据库终态可能已提交；重复取消不补发通知。当前没有事务消息/可靠投递补偿，也未新增同用户同会话的并发排序或缓存恢复能力。独立取消请求的 5 秒超时只限制浏览器等待，不能证明服务端没有执行取消。


### Workspace 基础课：首次检查待修正

已新增迁移 `d94e31b706fa_add_workspaces.py`（前置 `c83f20a915bd`）以及 `test_workspace_repository.py` / `test_workspace_migration.py`，覆盖名称、UUID、所有权、事务可见性与回滚、数据库约束、真实旧迁移链升级及旧表数据保持。配套文件 Ruff 通过。

从 apps/api 执行 `../../.venv/bin/python -W error::DeprecationWarning -m pytest -q tests/workspace/test_workspace_repository.py tests/migrations/test_workspace_migration.py`，在 conftest 导入模型阶段失败：`sqlalchemy.exc.ArgumentError: __table_args__ value must be a tuple, dict, or None`。没有测试通过计数，未连接测试数据库或执行迁移。还发现 User.workspaces 关系被误放到 Conversation、关系与仓储函数名称拼写不一致，已要求学习者修正。当前工作区不能沿用上一课 573 passed 作为本课通过证据；数据库尚未升级到新增 revision。

### Workspace 基础课：修正后验收（2026-09-15）

学习者修正元组和关系位置后，教练机械性统一剩余三处命名（User.workspaces、create_workspace、require_owned_workspace），按学习者原有注释位置补充字段、关系、约束和事务说明。名称规范化及所有权查询的业务逻辑保持学习者实现。

专项共 21 条：workspace 仓储 19 条，迁移 2 条。使用 PostgreSQL 隔离库/私有 schema，覆盖名称边界与中文、UUID、同名创建、双用户拒绝、跨连接提交可见性、调用方回滚、数据库约束及故障恢复；迁移测试执行真实旧迁移链，验证升级/回退后六张旧表记录保持，并演练迁移版本校准。迁移回退只在隔离库执行。

开发库升级前实测版本为 b62d19f804ae，login_sessions 已存在、workspaces 不存在，与之前交接中的版本描述不一致。根因未追溯。完整比对已有目标模型（含 server default），并核对登录会话三个 CHECK 约束后，确认 c83f20a915bd 的纯结构效果已存在。在隔离库验证同样路径后，使用带前置断言与版本表锁的单事务校准 c83f20a915bd，执行新增 Workspace 表迁移，再记录 d94e31b706fa。开发库没有执行降级、删表或重建，未读取业务表正文。最终 alembic check：No new upgrade operations detected。

这次校准不能当作通用 stamp 操作复用。另一台电脑需先执行 alembic current 并核对实际结构；正常情况下执行 alembic upgrade head 和 alembic check。若旧版本对应的表已存在，先定位差异，不能直接跳过迁移，尤其不能跳过数据回填类操作。

从 apps/api 运行：

```bash
../../.venv/bin/python -W error::DeprecationWarning -m pytest -q tests/workspace/test_workspace_repository.py tests/migrations/test_workspace_migration.py
../../.venv/bin/python -W error::DeprecationWarning -m pytest -q
../../.venv/bin/python -m ruff check app tests migrations
../../.venv/bin/python -m alembic current
../../.venv/bin/python -m alembic check
```

本课未修改前端，不重复前端和浏览器测试；上一课 171/84/7 为历史验证结果。Workspace 当前只有模型和仓储，尚无创建服务、HTTP/BFF、页面或文件目录绑定。

Workspace 最终全量回归：**594 passed，零弃用警告**；Ruff（app/tests/migrations）与 git diff --check 通过。

### Workspace 创建服务验收

`workspace_service.py` 由学习者实现；教练仅补空行、逗号和末尾换行，核心业务无需修正。新增 `tests/workspace/test_workspace_service.py` 20 条真实 PostgreSQL 隔离测试，成功与失败路径均已通过。

覆盖：提交后从独立连接读取；expire_on_commit 为 true/false 时不重新开启事务；结果脱离 Session 后可用、字段白名单与不可变性；名称错误保留分类；已有显式/只读/pending/flushed 事务不被提交或回滚；外键错误、真实 SQL 故障、提交前模拟故障及结果构造失败都撤销本次写入；Session 可复用；同名创建产生不同标识。测试不代表已解决“服务器提交成功但客户端未收到确认”的不确定结果或请求幂等。

首轮测试因 Docker 未运行、5432 连接拒绝而未进入业务断言；启动 Docker Desktop 和已有 PostgreSQL 容器后专项通过。测试只创建随机独立数据库/私有 schema，不连接开发业务表，不调用真实模型。本课没有新迁移，也未重跑前端或浏览器。

从 apps/api 运行：

```bash
../../.venv/bin/python -W error::DeprecationWarning -m pytest -q tests/workspace/test_workspace_service.py
../../.venv/bin/python -W error::DeprecationWarning -m pytest -q
../../.venv/bin/python -m ruff check app tests
```

Workspace 创建服务最终验收：后端全量 **614 passed，零弃用警告**；Ruff 与 git diff --check 通过。

### Workspace 创建 HTTP 接口：按影响范围验收

学习者实现 `routers/workspace.py` 和三个 schema；最初真实 app 请求 POST /workspaces 返回 404，原因是 main.py 缺少导入与 include_router。教练补齐这两行机械性装配，保留学习者的单数文件名 workspace.py，未改业务逻辑，并补末尾换行。

本次按影响范围执行 **58 passed，零弃用警告**：新增 test_workspace_api.py 29 条、现有 test_workspace_service.py 20 条、test_current_user_dependency.py 9 条。HTTP 测试使用真实 app（不进入访问开发库的 lifespan）、真实注册/签发 Cookie 和随机隔离 PostgreSQL；验证 201 与归属、同名允许、请求体身份字段拒绝、伪造查询参数/请求头不能改变身份、过期/撤销/无效凭证、精确 Origin/JSON 类型、输入与名称错误分类、SQL/提交/响应生成故障脱敏、线程池、认证与业务 Session 分离/关闭，以及 OpenAPI 实际注册。原始 HTTP 400 通过服务抛出异常模拟，JSON 语法错误按真实框架行为为 422。

```bash
# 从 apps/api
../../.venv/bin/python -W error::DeprecationWarning -m pytest -q tests/workspace/test_workspace_api.py tests/workspace/test_workspace_service.py tests/auth/test_current_user_dependency.py
../../.venv/bin/python -m ruff check app/routers/workspace/workspace.py app/schemas.py app/main.py tests/workspace/test_workspace_api.py
```

Ruff（本次修改文件）和 git diff --check 通过。未运行后端全量、前端、浏览器、模型/流式取消或迁移回归：本课新增独立 HTTP 路由与 schema，没有修改这些实现。614 passed 是上一课全量结果，不能当成本次全量结果。今后回归先按改动及调用链、共享依赖和风险选取；影响范围不清、共享机制或数据库结构变化时扩大范围。

提交后响应失败边界已通过测试确认：服务提交成功后，响应模型校验失败会返回脱敏 500，但记录仍已持久化。不可承诺所有 500 都回滚，也未实现创建请求幂等或自动重试补偿。没有开发业务表访问或真实模型调用。


### Workspace BFF 与注册页面闭环（2026-09-15）

- 新增注册 BFF 与登录页注册模式；注册成功后使用新账号登录，密码与确认密码清空，后端规范化用户名保留。不把创建成功等同于签发会话，超时不自动重试。
- Workspace BFF 用户误命名为 routes.ts，已修正为 route.ts，真实 Next HTTP 验证可达。
- 定向 BFF 测试 119 passed；TypeScript 和本轮修改文件 ESLint 通过。首次测试使用了教学示例中误写的 33 位 ID，已修正测试数据为 32 位；业务校验无需改变。
- 隔离 PostgreSQL + Chrome：新增 registration: 两场景通过，覆盖注册/重复注册/登录/首页返回/真实 Workspace 创建、密码确认、真实 422 和模拟 504；移动端截图检查通过。临时服务及数据库自动清理；未修改开发业务数据、未调用真实模型。

```bash
cd apps/web
node --experimental-strip-types --test 'test/features/workspaces/**/*.test.ts' 'test/features/auth/register-route.test.ts' 'test/features/auth/login-route.test.ts'
pnpm typecheck
pnpm exec eslint src/app/login/page.tsx src/app/api/auth/register/route.ts src/app/api/workspaces/route.ts test/features/auth/register-route.test.ts test/features/workspaces/create-route.test.ts
```

Workspace 单独回归可用 `pnpm test:workspaces`。浏览器从仓库根目录运行：

```bash
PLAYWRIGHT_MODULE=/Users/wanxiancheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
AUTH_TEST_FILTER='registration:' \
.venv/bin/python apps/web/test/browser/run-isolated.py
```


### PC 场景验收补充（2026-09-15）

用户明确产品使用场景为 PC，已写入 AGENTS.md。浏览器默认视口从 390×844 调整为 1440×900；注册专项增加 1366×768 与 1920×1080 的登录/注册截图、居中与宽度、横向溢出、首屏提交按钮及键盘 Tab 顺序检查。原移动端记录只是历史验证，不代表当前主验收场景。PC 注册截图已人工检查，保持现有居中表单布局。

本轮 registration: 三个 PC 场景全部通过：布局与键盘、新用户注册/重复注册/登录/Workspace 创建、输入错误及不确定结果恢复。临时服务与隔离数据库已自动清理；node --check 与 git diff --check 通过。


## 本地模式切换与验证（2026-09-15）

`setup_local.py` 保留原配置，自动生成并同步 `APP_MODE=local` 与 `LOCAL_RUNTIME_TOKEN`；两端 `.env` 文件均被 Git 忽略。本机身份与账号身份的数据分开，不转移历史归属。`run_local.py` 只绑定 127.0.0.1，Ctrl+C 关闭 Web/API，持久数据保留。先用 `docker compose -f infra/compose.yaml up -d` 启动 PostgreSQL/Redis，使用当前 `.venv` 完成迁移，再运行启动器。Compose 端口已收紧为回环地址；不会删 Volume。

本轮修改共享身份依赖与全局边界，后端全量 656 passed、零弃用警告；原账号前端/BFF 204 passed，Workspace/本地 BFF 65 passed；TypeScript、修改文件 ESLint/Ruff 通过。测试固定账号基线，本地专项自行切换，避免本机 `.env` 改变既有测试语义。

```bash
cd apps/api
../../.venv/bin/python -W error::DeprecationWarning -m pytest -q
cd ../web
APP_MODE=account pnpm test:auth
APP_MODE=account pnpm test:workspaces
pnpm typecheck
```

本地 PC 浏览器专项从仓库根目录运行（使用隔离 PostgreSQL 和模拟模型，不读写开发业务表）：

```bash
BROWSER_APP_MODE=local \
PLAYWRIGHT_MODULE=/Users/wanxiancheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
.venv/bin/python apps/web/test/browser/run-isolated.py
```

账号浏览器回归不设置 BROWSER_APP_MODE（默认为 account）；可用 AUTH_TEST_FILTER 选择受影响场景。不要直接对开发业务数据运行测试。


最终验收补充：本地 PC 浏览器 3 场景通过（无 Cookie 真实聊天/取消、创建/校验/刷新、模拟超时不重试）；原账号模式 1 个真实登录/错误密码/刷新/登出场景通过。配置生成保留模型 Key、同步随机凭证及重复执行稳定性测试另计 1 passed，不冒称重跑了后端全量。截图检查覆盖 1366×768/1920×1080。

已重启当前开发 Web/API 为 127.0.0.1:3000/8000；Compose 容器保留命名卷重建后健康，端口为 127.0.0.1:5432/6379。实测首页、创建页、本机身份接口 200，登录页返回首页；裸 API 无内部凭证 403，前端 HTML/JSON 无内部凭证或登录 Cookie。首次本机身份探测仅创建本机主体，没有迁移历史账号数据。当前运行实例已准备好继续学习。


### Workspace 列表后端验收（2026-09-15）

GET /workspaces 已完成，默认返回 20 条、上限 100；过滤身份后按 created_at/id 倒序，多查一条判断 has_more。列表读取不 commit、不刷新调用方 pending 写入，Session 关闭前转为响应字段。仅完成后端，没有列表 BFF/页面或翻页。

`tests/workspace/test_workspace_list.py` 新增 21 条，和 `test_workspace_api.py`、`test_workspace_repository.py`、`test_local_mode.py` 组成 82 passed（10.15s）、零弃用警告，Ruff/diff check 通过。覆盖空列表、默认/极值/截断、相同时间排序、两类身份隔离、非法参数、SQL/响应故障脱敏、Session 释放与只读边界。核心代码无需修正，仅补格式。未重跑全量或前端。

```bash
cd apps/api
../../.venv/bin/python -W error::DeprecationWarning -m pytest -xq --tb=short tests/workspace/test_workspace_list.py tests/workspace/test_workspace_api.py tests/workspace/test_workspace_repository.py tests/local/test_local_mode.py
```

### Workspace 列表 BFF 与 PC 页面验收（2026-09-15）

列表闭环已完成，替代上一条“仅后端”的阶段状态。GET BFF 校验身份、来源、limit 与公开响应，区分失败/超时/取消；页面区分加载、空列表与错误，支持刷新、创建后返回列表及账号登录返回。最新 20 条以 has_more 提示截断，尚无翻页。

本课 `APP_MODE=account pnpm --dir apps/web test:workspaces`：116 passed，其中新增 list-data 17 条、list-route 34 条。覆盖合法/非法数据、Unicode 名称、重复 ID、两种身份、错误映射、超时与取消。TypeScript 与定向 ESLint/Ruff、node --check、git diff --check 通过；用户核心逻辑无需修正，仅补末尾换行。

隔离 PostgreSQL + Chrome：本地 4/4、账号 5/5 场景通过。覆盖真实空列表→创建→返回→刷新、错误不误作空列表、重试与畸形响应、请求中重复刷新/导航、真实 21 条截断，以及账号失效重新登录返回。PC 检查覆盖 1366×768/1920×1080，本地截图已人工检查。临时服务与数据库已清理，没有调用真实模型或修改开发业务数据；本课未重跑后端全量与聊天全量。

从仓库根目录分别执行以下命令；Playwright/Chrome 路径配置同前文：

```bash
BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=workspace-list.mjs .venv/bin/python apps/web/test/browser/run-isolated.py
BROWSER_APP_MODE=account BROWSER_TEST_SCRIPT=workspace-list.mjs .venv/bin/python apps/web/test/browser/run-isolated.py
```

隔离启动器现在保留真实 src/app 与 src/features 目录关系，避免相对导入在临时应用中失效。`scripts/run_local.py` 已启用 uvicorn --reload；Python 源码更新可自动加载，配置或依赖变化仍需手动重启。本轮发现旧 API 进程未加载新增 GET 接口，已正常停止原启动器并重启 Web/API；实际 /workspaces 与 /api/workspaces?limit=1 均返回 200，后者保留 Cache-Control: no-store。

### 本地项目目录校验服务验收（2026-09-15）

新增 test_workspace_directory.py，29 passed（0.04s）、零弃用警告。真实临时目录覆盖中文/首尾空格、普通文件、缺失目录、目录链接、链接后的 ..、断链、循环链接和根目录；故障注入覆盖 resolve/stat 阶段权限、目录消失与其他系统异常，并检查安全错误文本。成功路径保留文件内容与修改时间，失败路径不创建缺失目录。临时目录由 pytest 管理，不使用数据库 fixture、不启动开发服务、不调用模型。Windows 原生盘符与链接行为尚未实机验证。

```bash
cd apps/api
../../.venv/bin/python -W error::DeprecationWarning -m pytest -q tests/workspace/test_workspace_directory.py
../../.venv/bin/python -m ruff check app/services/workspace/workspace_directory.py tests/workspace/test_workspace_directory.py
```

核心实现与参考一致，仅补空行及末尾换行；定向 Ruff 和 git diff --check 通过。首次从根目录执行 pytest 未设置 app 导入路径，收集失败；切到 apps/api 按上面命令运行后通过。本课不涉及数据库、前端或账号模式，未重复其回归。当前只完成目录校验，尚无目录绑定字段、接口或文件工具授权。

### Workspace 目录字段首次检查：待修正

模型 root_path 字段及新迁移内容符合参考，但 Workspace 的约束误写到 User 类，覆盖旧用户约束，Workspace 自身缺少新约束。test_workspace_root_path.py 结果 1 passed / 2 failed，明确复现约束归属错误。未连接或升级开发数据库，未运行账号流程。等待学习者修正后继续真实 PostgreSQL 迁移验收；目前不标记课程完成。

### Workspace 目录字段修正后验收（2026-09-15）

用户已将 Workspace 约束移回正确模型，并恢复 User 既有约束。新增目录字段测试与历史迁移、仓储、创建服务、列表组成 66 passed（6.11s）、零弃用警告，定向 Ruff 与 diff check 通过。真实隔离 PostgreSQL 先提交旧用户/Workspace，再升级；验证旧值逐字段保持、root_path 默认 NULL、空字符串触发指定 CHECK、普通文本可提交、原创建读取可用。回退/再次升级仅在隔离库执行，验证旧列记录仍保留、绑定字段回退后丢失、重升为 NULL。历史迁移测试改用该历史版本模型快照。

```bash
cd apps/api
../../.venv/bin/python -W error::DeprecationWarning -m pytest -xq tests/workspace/test_workspace_root_path.py tests/migrations/test_workspace_migration.py tests/workspace/test_workspace_repository.py tests/workspace/test_workspace_service.py tests/workspace/test_workspace_list.py
```

开发库原为 d94e31b706fa，升级前实际结构及 CHECK 与旧版本快照一致。执行 alembic upgrade e05f42c817ab 成功，current 为 e05f42c817ab (head)，alembic check 无差异；未执行 stamp、回退或业务数据回填。实际 /workspaces 和 /api/workspaces?limit=1 均为 200，未启动额外开发服务。其他电脑仍需各自执行正常升级。此次未跑账号流程、完整前端、浏览器交互或完整后端回归。

### 本地 Workspace 目录绑定事务服务验收（2026-09-15）

用户核心代码与参考一致，无需修正。test_workspace_binding.py 新增 16 条，与目录校验/仓储/创建服务组成 84 passed（2.87s）、零弃用警告；定向 Ruff 和 git diff --check 通过。测试使用随机独立 PostgreSQL/私有 schema 与临时目录，允许真实提交并自动清理，未操作开发业务数据。

覆盖首次提交及脱离 Session 的冻结结果、普通路径与符号链接重复绑定、不同路径冲突、无效/已删除目录、资源不可访问时不检查文件系统、已有事务不被回滚、flush 后提交故障回滚与重试、行锁查询刷新旧 ORM 值且不刷新其他待写入对象。并发用 pg_blocking_pids 确认第二连接真实等待，再释放首请求；同路径成功，不同路径冲突，最终值保持首个提交结果。

```bash
cd apps/api
../../.venv/bin/python -W error::DeprecationWarning -m pytest -xq tests/workspace/test_workspace_binding.py tests/workspace/test_workspace_directory.py tests/workspace/test_workspace_repository.py tests/workspace/test_workspace_service.py
```

本课只完成服务层，尚未接 HTTP/BFF 或页面；未新增迁移、启动额外开发进程或运行账号/UI/模型全量回归。行锁仅保护数据库绑定决策，文件系统仍可能在检查后变化；目录内文件访问边界尚待实现。

### 本地目录绑定 HTTP 验收（2026-09-15）

新增 test_workspace_binding_api.py 32 条；与绑定事务、目录校验及本地列表回归组成 78 passed（3.77s）、零弃用警告，定向 Ruff 与 diff check 通过。用户核心实现无需修正，仅补末尾换行，既有列表测试适配统一校验文案。

```bash
cd apps/api
../../.venv/bin/python -W error::DeprecationWarning -m pytest -xq tests/workspace/test_workspace_binding_api.py tests/workspace/test_workspace_binding.py tests/workspace/test_workspace_directory.py tests/workspace/test_workspace_list.py::test_local_list_keeps_registered_resources_separate
```

使用真实 app/本地身份与自动清理的隔离 PostgreSQL，不运行开发库 lifespan。覆盖创建→绑定→重复→列表白名单，冲突保持原值，正文额外字段/类型/空值、标识格式、真实目录失败、资源不可访问、Host/Origin/内部凭证、非 local 提前拒绝、JSON 类型/解析、目录故障映射和未知错误脱敏。验证身份 Session 在业务前关闭，业务失败及响应构造时 Session 均关闭。提交后响应构造故障返回安全 500，但独立数据库查询证实记录已绑定，响应文案因此使用“结果未确认”。

本课未启动额外服务或改动开发业务数据，无新增迁移；尚未接 BFF/页面，未跑账号功能、前端或浏览器回归。

### 本地目录绑定 BFF 验收（2026-09-15）

新增 binding-route.test.ts：60 passed，TypeScript（next typegen + tsc）和定向 ESLint、diff check 通过。用户核心实现无需修正，仅补末尾换行。测试覆盖本地边界、动态标识、严格正文、安全请求头和响应白名单、请求/响应 Workspace ID 一致、Unicode 字数和绝对路径、15 类已知错误映射及状态错配/原型属性拒绝、网络/畸形 JSON，以及客户端/超时在上游请求或正文读取阶段的中断。写请求不自动重试；错误结果保持未确认语义。

```bash
node --experimental-strip-types --test apps/web/test/features/workspaces/binding-route.test.ts
pnpm --dir apps/web typecheck
```

本课只做 BFF 模拟上游专项，不把它记为真实 HTTP/浏览器集成验收；未运行账号功能或完整后端/前端测试，未启动开发服务或操作开发业务数据。非 local 拒绝测试属于本地端点边界。类型生成提示当前 Node 通过 Rosetta 运行，但类型检查正常通过。

### 三栏工作台首次验收：待修正

TypeScript、定向 ESLint 与聊天 84 条通过。新增 workbench.mjs：PC 初始 1366×768/1920×1080 尺寸及输入可见性通过，1366 截图已检查；收起左栏后中栏宽度为 0，侧栏用例失败。真实本地聊天、取消 204、重新生成通过；浏览器结果 1 passed / 1 failed，隔离服务及 PostgreSQL 已清理。原因是教练参考遗漏 Grid 固定列位置，hidden 左栏退出自动排布，需给三栏分别设置固定列与行。未替学习者修改核心布局，待其修正后继续完整浏览器验收。隔离启动器本地模式移除额外测试导航以匹配实际视口结构。

### 三栏列定位修正后验收

用户授权直接修正。workbench-shell.tsx 给左/中/右栏分别指定 col-start-1/2/3 和 row-start-1，修复 hidden 导致自动排布进入 0px 第一列；清理本课展示区多余缩进。TypeScript、定向 ESLint、浏览器配套 Ruff/node --check/diff check 通过。

浏览器 3 类场景最终通过：PC 两种尺寸/全部折叠组合/键盘操作/草稿保留；真实聊天及运行中折叠、取消与重试；真实测试模型输出 200 行内容，中栏独立滚动且输入框位置不变，并检查深色 1920×1080。浅色初始与深色长回复截图已查看。完整首轮为 2 passed / 1 failed，失败因 2 秒模型在切栏时已结束；模拟等待改 20 秒后单独复跑真实聊天场景 1 passed。无产品逻辑改动用于绕过该测试。隔离服务与数据库已清理；没有新开用户开发服务。此前聊天 84 条已通过，列定位修正后未重复无关全量。

```bash
BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=workbench.mjs .venv/bin/python apps/web/test/browser/run-isolated.py
```

Playwright/Chrome 配置同前文；WORKBENCH_TEST_FILTER 可选择场景，完整运行不设置。当前只完成工作台骨架及真实运行详情，项目列表/目录绑定入口和调宽后续接入。

### 真实 Workspace 项目侧栏验收（2026-09-15）

核心实现符合参考，仅补末尾换行。列表解析 17 条通过，TypeScript/定向 ESLint、浏览器脚本语法及 diff check 通过。新增 workspace-sidebar.mjs 共 4 场景全部通过：真实空列表→创建→返回侧栏→选择→刷新保留→折叠保留草稿，1366×768/1920×1080 布局；503 与畸形列表不误作空状态、重试恢复；20 项截断/长名称无横向溢出/新列表移除选择/聊天草稿保留；请求中刷新禁用且不重复 fetch、离开页面后旧结果不污染重新挂载的侧栏。1366 PC 截图已人工检查。

```bash
node --experimental-strip-types --test apps/web/test/features/workspaces/list-data.test.ts
BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=workspace-sidebar.mjs .venv/bin/python apps/web/test/browser/run-isolated.py
```

Playwright/Chrome 路径配置同前文。临时服务与隔离 PostgreSQL 已清理；未启动额外开发服务或修改开发业务数据，未重复账号/完整后端/聊天全量测试。当前选择仅展示项目资料；列表不含 root_path，绑定状态回读与表单仍待接入。

### 目录绑定状态读取 HTTP 验收（2026-09-15）

用户核心符合参考，仅补末尾换行。新增 test_workspace_directory_api.py 17 条，与绑定 HTTP 32 条及本地列表 1 条共 50 passed（4.25s）、零弃用警告；定向 Ruff 和 diff check 通过。

```bash
cd apps/api
../../.venv/bin/python -W error::DeprecationWarning -m pytest -xq tests/workspace/test_workspace_directory_api.py tests/workspace/test_workspace_binding_api.py tests/workspace/test_workspace_list.py::test_local_list_keeps_registered_resources_separate
```

真实 app/隔离 PostgreSQL 验证无 Origin 的合法 GET、NULL 与绑定路径回读、目录删除后仍读取保存状态、标识及资源/Host/Origin/凭证边界、非 local 提前拒绝、身份和业务 Session 关闭、读取禁止 commit，以及查询/响应异常固定 500 而非 NULL。另一连接持有写锁且 flush 未提交时，读取在 1 秒 statement_timeout 内返回先前已提交 NULL，验证未使用 FOR UPDATE。绑定 PUT 及原本地创建/列表仍通过。未启动额外服务、写开发业务数据、新增迁移或回归账号功能/UI；夹具自动清理隔离数据库。


### 目录状态读取 BFF 验收（2026-09-15）

新增 directory-read-route.test.ts 50 条，与原绑定 PUT 60 条共 110 passed。覆盖 null/绝对路径、缺字段及畸形响应、响应 ID 匹配、Unicode 名称边界、本地凭证与来源检查、错误白名单、脱敏、无缓存、不转发 Cookie/查询参数，以及请求前、fetch、正文读取和正文返回后的取消/超时。失败不自动重试。上游 fetch 使用模拟，未进行浏览器/API 集成或数据库验证。

```bash
node --experimental-strip-types --test apps/web/test/features/workspaces/directory-read-route.test.ts apps/web/test/features/workspaces/binding-route.test.ts
pnpm --dir apps/web typecheck
cd apps/web
pnpm exec eslint 'src/app/api/workspaces/[workspaceId]/directory/route.ts' test/features/workspaces/directory-read-route.test.ts
```

TypeScript 和定向 ESLint 通过；首轮类型检查发现教练新增测试的异构对象数组推断问题，显式标注 Record<string, string>[] 后通过。用户核心无需修正，仅补末尾换行。本课没有启动额外开发服务或修改数据库，下一课接入工作台目录面板。


### 系统目录选择与自动保存验收（2026-09-15）

用户明确授权教练直接替换手输路径交互。项目侧栏现在点击“选择项目目录”打开系统文件夹选择器，选中后自动校验并保存；取消返回 204，无额外绑定按钮。macOS 使用固定 osascript 脚本，Windows 使用系统 PowerShell STA FolderBrowserDialog，无新依赖。选择等待 120 秒，BFF/页面分别 130/140 秒；单 API 进程窗口互斥，不在窗口等待期间占用数据库 Session。Windows 已覆盖输出协议和异常测试，但尚未实机验证窗口。当前本机页面已观察到用户选择后显示已绑定目录；没有代替用户更换现有项目目录。

后端选择器/选择 HTTP/原读取与绑定共 72 passed（4.62s），前端新选择 BFF 25 条加原 GET/PUT 共 135 passed；TypeScript、定向 ESLint/Ruff、脚本语法与 diff check 通过。首轮新增 BFF 测试的对象数组类型推断错误已修正。Python 首次从根目录运行因 app 导入路径失败，改从 apps/api 按规范执行后通过。

```bash
cd apps/api
../../.venv/bin/python -m pytest -q tests/workspace/test_directory_picker.py tests/workspace/test_workspace_selection_api.py tests/workspace/test_workspace_directory_api.py tests/workspace/test_workspace_binding_api.py
cd ../..
node --experimental-strip-types --test apps/web/test/features/workspaces/directory-select-route.test.ts apps/web/test/features/workspaces/directory-read-route.test.ts apps/web/test/features/workspaces/binding-route.test.ts
BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=workspace-directory.mjs .venv/bin/python apps/web/test/browser/run-isolated.py
```

Playwright/Chrome 配置同前文。最终浏览器 6/6 场景通过：取消→重新选择→真实保存→刷新保留及两种 PC 尺寸；真实 POST 提交后丢失响应再 GET 恢复；畸形读取不误报未绑定；路径拒绝/冲突/畸形成功/网络异常；旧 GET/POST 晚返回不能污染新项目、连续点击去重及草稿保留；GET 与 POST 正文读取超时恢复。只有系统目录选择结果使用隔离测试替身，真实场景其余 BFF/API/目录校验/数据库均使用正式实现。截图 directory-1366.png 已检查，位于 /private/tmp/agent-ui-preview/output/playwright/。

旧手输版本初轮 5/6 通过后按用户要求改版；新版本首轮 5/6，失败为浏览器点击等待 10 秒超时，最终增加操作等待上限并明确状态等待后完整复跑 6/6。没有通过改产品逻辑跳过失败。所有临时 Web/API 和隔离 PostgreSQL 已清理；没有新增常驻开发服务。当前 React 类型中 FormEvent 已 deprecated，用户改用 React SubmitEvent 合理；最终页面改为按钮选择，无需表单事件类型。


### Task 模型与兼容迁移验收（2026-09-15）

用户已将 task relationship 从 Workspace 移到 Conversation，mapper 配对恢复。新增 test_task_model.py、test_task_migration.py，并更新旧迁移测试的历史 metadata 快照，避免与最新 Task 模型误比较。相关测试最终 41 passed（3.02s），使用 -W error，零警告；Ruff/diff check 通过。首次 40 条通过但测试构造 Task 后又读取 User 触发 autoflush 警告，已将查询置于对象构造之前；非产品逻辑问题。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -q tests/tasks/test_task_model.py tests/migrations/test_task_migration.py tests/migrations/test_workspace_migration.py tests/workspace/test_workspace_root_path.py tests/workspace/test_workspace_repository.py tests/runtime/test_run_repository.py
../../.venv/bin/python -m alembic check
```

隔离 PostgreSQL 使用真实历史迁移，保存旧项目/会话/消息/运行/事件后升级，验证旧值保持、多个 NULL 关联、单任务唯一会话、外键/非空/标题边界、200 个 Unicode 字符、同名任务、ORM commit 后重读、旧版会话创建、回退再升级。另补开发库空 Task 表恢复演练。

开发库检查：版本为 e05f42c817ab，但启动 init_db/create_all 已提前创建 tasks 空表，conversations 缺少 task_id。没有盲目 upgrade 或直接 stamp：先比对完整部分结构，再单事务设置 5 秒 lock_timeout、锁 tasks、确认 count=0，移除仅此空表，确认上一版结构完全一致，运行 f16a53d928bc 的真实 upgrade，确认最新模型无差异后才更新版本账本并提交。失败可整体回滚。提交后 head=f16a53d928bc、Task 0 条、已关联会话 0 条，alembic check 无差异。没有修改旧业务行、回退开发数据库或启动额外服务。

此为严格前置核对后的特例修复，不是通用迁移命令。当时 API 启动 create_all 的职责尚未收口；现已在 2026-09-16 改为只读迁移版本检查，见后文。其他电脑仍须分别核对并升级，不能复制本机版本账本。


### Task 创建事务服务验收（2026-09-15）

新增 test_task_service.py 25 条，与 Task 模型、目录绑定事务和运行仓储共 46 passed（3.01s），-W error 零警告；Ruff/diff check 通过。学习者核心符合参考，仅补末尾换行。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -q tests/tasks/test_task_service.py tests/tasks/test_task_model.py tests/workspace/test_workspace_binding.py tests/runtime/test_run_repository.py
../../.venv/bin/python -m ruff check app/services/tasks/task_service.py tests/tasks/test_task_service.py
```

隔离 PostgreSQL 验证 Task/Conversation 同事务真实保存与正确归属，expire_on_commit 两种配置下不重开事务，关闭 Session 后普通不可变结果仍可读取；标题规范化/Unicode 边界/类型拒绝/同名新任务；未知和他人项目拒绝优先于标题校验；显式、读取、待写及已 flush 的调用方事务不被提交或回滚。Conversation before_insert 期间确认 Task 已插入，再执行真实无效 SQL，独立连接验证两表均无残留且 Session 可恢复。结果构造失败与模拟 commit 抛错也回滚两条已 flush 记录；此处不证明连接在真实提交成功后断开时可以确定结果。

本课没有 HTTP/BFF/UI 变更或新迁移，未写开发业务数据、调用模型或启动额外服务；夹具自动清理隔离数据库。


### Task 创建 HTTP 验收（2026-09-15）

test_task_api.py 最终 33 条。首轮 Task 32 条 + 绑定 HTTP 32 + 目录读取 17 + 系统选择 11，共 92 passed（7.09s）。补 FastAPI 响应模型校验失败后，Task 33 + 本地创建/列表各 1 条复跑 35 passed（3.60s），相关覆盖合计 95 条，均 -W error 零警告。Ruff/diff check 通过，用户核心无修正，仅空白和末尾换行。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -q tests/tasks/test_task_api.py tests/workspace/test_workspace_binding_api.py tests/workspace/test_workspace_directory_api.py tests/workspace/test_workspace_selection_api.py
../../.venv/bin/python -W error -m pytest -q tests/tasks/test_task_api.py tests/local/test_local_mode.py::test_local_identity_stable_and_does_not_adopt_registered_user tests/workspace/test_workspace_list.py::test_local_list_keeps_registered_resources_separate
```

真实应用和隔离 PostgreSQL 验证 201 保存 Task/Conversation、标题规范化和 Unicode 边界、额外字段/类型/路径拒绝、未知与他人项目统一 404、Cookie/身份头不能改变归属、Host/Origin/内部凭证和非 local 提前拒绝。身份 Session 在服务前关闭，业务 Session 在响应构造前关闭。服务失败不产生记录；真实提交后响应构造失败、服务结果字段异常及 FastAPI 已注册 response_model 校验失败均为安全 500 task_creation_uncertain，独立连接确认两条记录仍已保存。重复同标题创建不同任务，未实现幂等。

未运行模型、迁移或浏览器，不改开发业务数据，不启动额外服务。测试数据库由夹具自动清理。下一课 Task 创建 BFF。


### Task 创建 BFF 验收（2026-09-15）

新增 task-create-route.test.ts 59 条全部通过（627.79ms），pnpm typecheck、定向 ESLint 和 diff check 通过。学习者核心无需修正，仅末尾换行。

```bash
node --experimental-strip-types --test apps/web/test/features/workspaces/task-create-route.test.ts
pnpm --dir apps/web typecheck
cd apps/web
pnpm exec eslint 'src/app/api/workspaces/[workspaceId]/tasks/route.ts' test/features/workspaces/task-create-route.test.ts
```

使用真实 Request/Response、模拟上游 fetch，验证 201 与公开字段、服务器凭证注入、Cookie/查询参数不透传、Origin/Host/配置/JSON/标识/额外字段拒绝、标题原样转发、Unicode 响应长度、项目标识匹配、错误码和状态组合、自有属性校验、畸形响应/网络失败。取消覆盖转发前、请求正文、fetch、响应正文及正文返回竞态，超时覆盖上游请求与正文，全部不自动重试；前置取消明确未提交，后置取消或超时保持结果未确认。

本课未修改共享 BFF 工具或后端，未重复无关接口回归，未运行浏览器/真实 API 集成，未启动额外服务或访问数据库。真实联调留到工作台创建入口课程。


### 工作台 Task 创建入口验收（2026-09-15）

新增 apps/web/test/browser/workspace-task.mjs，隔离启动器复制真实 Task BFF。5 组浏览器场景通过；首轮发现目录面板/任务表单的同级 key 重复，补组件前缀并将 key/水合控制台警告纳入断言后，全场景复跑 5 passed、0 failed。TypeScript、定向 ESLint、Task BFF 59 条、git diff --check 通过。

```bash
BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=workspace-task.mjs \
PLAYWRIGHT_MODULE=/Users/wanxiancheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
.venv/bin/python apps/web/test/browser/run-isolated.py
pnpm --dir apps/web typecheck
node --experimental-strip-types --test apps/web/test/features/workspaces/task-create-route.test.ts
```

浏览器真实经过 BFF/API/独立 PostgreSQL：未绑定目录的项目也可创建任务，空白及 201 个 Unicode 字符被拒绝，首尾空白规范化后 200 个 Emoji 成功；再次主动创建返回不同 Task/Conversation 标识。真实提交返回 201 后主动丢弃响应，表单进入结果未确认且阻止再次提交。此处不模拟真实数据库 commit 丢包。

模拟故障覆盖 12 种异常成功/状态错误码错配响应，明确拒绝恢复编辑且不显示后端原始 message；同一事件循环双提交仅发一次，忽略 abort 的旧项目响应不污染新表单，正文阶段超时同样暂停提交。真实模型不参与本课。

已查看 1366×768 与 1920×1080 截图，三栏无横向溢出，长标题在侧栏内换行，聊天输入保持可见且草稿不丢失。截图位于 /private/tmp/agent-ui-preview/output/playwright/task-1366.png 和 task-1920.png。临时服务、隔离 schema/database 已自动清理，没有写开发业务数据。

当前创建结果不自动切换聊天，无 Task 列表与恢复入口；组件级防重复不是请求幂等，刷新/重新挂载会丢失未知状态提示。Next.js 仍提示当前 Node 经 Rosetta 运行，未将此提示记作业务测试失败。

### 对话式任务侧栏交互修正（2026-09-15）

用户明确授权教练直接实现。移除旧标题表单，使用共享 shadcn/ui Button 的 ghost/icon 样式组合项目行、笔形新建入口及任务列表；目录管理移入项目设置。首次发送才创建 Task/Conversation，后续消息固定会话，点回任务读取历史；首轮完成后后端生成标题，失败保留临时摘录，条件更新防止覆盖后来标题。前后端字段校验与本地授权均保留，没有新增迁移。

验证结果：后端 Task 工作台/创建/本地入口共 57 passed（4.50s），-W error；前端 Task 创建/读取 BFF 与聊天状态/终态共 158 passed；TypeScript、定向 ESLint、Ruff、diff check 通过。最初新服务 SessionLocal 未纳入本地测试夹具，导致读取归属检查失败；已在共用 local_client 中显式替换为隔离库 sessionmaker，复跑通过，未执行业务写入或迁移。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -q tests/tasks/test_task_workspace.py tests/tasks/test_task_api.py tests/local/test_local_mode.py
cd ../..
node --experimental-strip-types --test apps/web/test/features/workspaces/task-read-route.test.ts apps/web/test/features/workspaces/task-create-route.test.ts apps/web/test/features/chat/*.test.ts
pnpm --dir apps/web typecheck
BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=workspace-task.mjs \
PLAYWRIGHT_MODULE=/Users/wanxiancheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
.venv/bin/python apps/web/test/browser/run-isolated.py
```

workspace-task.mjs 已替换为新交互的 4 组浏览器测试，初轮及补迟到历史用例后的复跑均 4 passed、0 failed。真实 BFF/API/隔离 PostgreSQL，聊天和标题仅模拟模型出口；验证双提交只创建一次、两轮同会话、自动标题保存、刷新后手动选回任务、两个项目隔离、设置中的目录入口、空草稿、已提交响应丢失后从列表恢复，以及历史失败阻止发送、忽略 abort 的旧历史迟到仍不污染新任务。临时服务、schema/database 已自动清理。

已视查 1366×768 与 1920×1080，截图为 /private/tmp/agent-ui-preview/output/playwright/task-conversation-1366.png 与 task-conversation-1920.png。布局开关提升到工作台上下文，切换任务保留侧栏开关；静态检查通过。本次没有调用真实付费模型验证标题措辞质量。辅助标题请求的费用尚未计入 AgentRun 指标。刷新自动选回原 Task、历史运行摘要恢复、删除及服务端创建幂等仍待后续课程；当前可从真实列表手动选回任务。

### 空白页布局、侧栏字号与键盘交互优化（2026-09-15）

空白态引导/输入框居中相邻；有消息后保持底部输入框。详情默认收起，通过图标打开；侧栏 280px，项目/任务 15px、辅助 13px、品牌 16px。项目分组内放刷新和新增图标，项目行显示展开箭头。输入区显示项目及键盘提示，Enter 发送、Shift+Enter 换行，isComposing/229 阻止输入法确认误发。

TypeScript、定向 ESLint、diff check 与聊天 84 条专项通过。沿用 workspace-task.mjs 的 4 组真实 BFF/API/隔离 PostgreSQL 浏览器场景，全部通过；追加空白页标题/输入框间距、三个视口无横向溢出、键盘换行与输入法确认不创建任务的检查。用户要求放大侧栏字体后，使用 BROWSER_SCENARIO='first send' 定向复跑 1 组通过，计算样式确认项目字号为 15px。临时服务和隔离库均自动清理。

已查看 1366×768 和 2560×1318 空白页截图；同时生成 1920×1080 截图。最终图片为 /private/tmp/agent-ui-preview/output/playwright/task-empty-1366.png、task-empty-1920.png、task-empty-2560.png。后端未变更；未重复运行无关数据库专项。运行方式沿用上一节，在浏览器命令前加 BROWSER_SCENARIO='first send' 可仅复跑主要布局与发送场景。不改变下一课，也不将简单样式调整另行归档面试题。

### 全页 18px 字号（2026-09-15）

按用户明确要求，全站 xs/sm/base 与 body 使用 18px，并替换工作台小于 18px 的固定字号；标题保留更大层级。同步增加 Button/Input 高度、侧栏行高，左右栏展开宽度调整为 320px/340px。TypeScript、定向 ESLint、diff check 通过。BROWSER_SCENARIO='first send' 浏览器定向复跑通过，计算样式确认项目名 18px；1366×768、1920×1080、2560×1318 布局检查与截图完成，1366 截图已视查。首发、同会话续聊、标题及历史链路正常；临时服务和隔离库已清理。此前 15px/280px 样式记录由本节取代。


### Task 详情读取接口验收（2026-09-15）

学习者实现核心，教练新增 test_task_detail.py 18 条；详情、任务工作台 11 条及创建 33 条共 62 passed（5.41s），-W error 零警告。Ruff/diff check 通过，核心无需修正，仅补 schemas.py 末尾换行。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -q tests/tasks/test_task_detail.py tests/tasks/test_task_workspace.py tests/tasks/test_task_api.py
../../.venv/bin/python -m ruff check app/schemas.py app/services/tasks/task_workspace.py app/routers/workspace/workspace.py tests/tasks/test_task_detail.py
```

复用 local_client 的隔离 PostgreSQL 会话工厂，验证真实 HTTP 与公开字段、Cookie/身份头不改变归属、22 条任务中第一页外的旧任务可直接定位、未知任务/项目/错误项目/他人项目/会话归属错配统一 404、非法路径 422、非本地模式提前拒绝及内部异常脱敏。直接服务测试捕获 SQL，确认仅 SELECT、未 commit，Session 已关闭且序列化不再触发查询；重读标题保持不变。测试库自动清理；未访问开发业务表、运行迁移、调用模型或启动额外服务。本课不涉及前端，不运行浏览器或无关全量回归。

### 按业务领域整理目录与当日收尾（2026-09-15）

用户授权后，将 routers/services/repositories 按领域分组，并将后端测试归入 auth/chat/core/local/migrations/model/runtime/tasks/tools/workspace。共迁移 97 个源码/测试文件；完整布局见 docs/project-structure.md。同步 Python 导入、测试夹具包导入、浏览器隔离启动器、迁移测试文件定位和文档中的运行路径，不保留旧路径转发模块。

迁移后后端全量 899 passed（58.07s），-W error；Ruff 全量 app/tests 与 diff check 通过。前端 TypeScript、Workspace 325 条通过。BROWSER_SCENARIO='first send' 浏览器复跑 1 组通过，真实 BFF/API/隔离 PostgreSQL 验证创建、连续聊天、自动标题和刷新后历史读取，包含三个 PC 尺寸布局检查；模型出口仍为模拟，临时进程及测试库自动清理。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -q
../../.venv/bin/python -W error -m pytest -q tests/tasks
../../.venv/bin/python -m ruff check app tests
cd ../web
pnpm typecheck
pnpm test:workspaces
```

启动命令仍为 python -m uvicorn app.main:app --reload，不改变接口 URL、数据库模型或 Alembic 修订。若已有进程未启用 reload，重启原服务即可，不需要额外创建服务或重新初始化数据库。当日进度更新在 LEARNING_HANDOFF.md，学习内容与完成范围更新在 LEARNING_CURRICULUM.md；唯一下一课保持 Task 详情 BFF。


### Task 详情 BFF 验收（2026-09-16）

学习者实现 readTaskDetail、taskProxy 的 detail 分支与同源详情 GET 路由；教练补 task-detail-route.test.ts 58 条。详情与既有读取代理共 73 条通过；Workspace 全量 383 条通过，TypeScript、全量 ESLint 与 diff check 通过。核心逻辑无需修正，仅补新路由文件末尾换行。

```bash
cd apps/web
node --experimental-strip-types --test test/features/workspaces/task-detail-route.test.ts test/features/workspaces/task-read-route.test.ts
pnpm test:workspaces
pnpm typecheck
pnpm lint
```

测试直接导入真实 GET 路由并模拟上游 fetch，覆盖公开字段白名单、Unicode 长度、项目/任务错配、会话标识格式、非法参数提前拒绝、本地凭证与来源、no-store、上游状态/正文脱敏和不重试。用可控 AbortSignal 验证浏览器取消与 20 秒超时预算，分别覆盖 fetch 等待、响应正文读取及 JSON 完成后的取消检查。既有列表/messages/title 随全量测试回归。

本轮没有修改后端或 UI，未重复执行数据库/浏览器测试，也未调用模型；本记录不代表已完成真实浏览器刷新恢复。下一课接 URL 选中状态与刷新自动恢复。


### URL 选中状态与刷新自动恢复验收（2026-09-16）

学习者完成 URL 解析/写入、工作台恢复与错误态、侧栏展开。首次静态检查发现参考代码在 effect 中同步 setState；教练调整为挂载后微任务，并用 active 标记阻止 StrictMode 已清理挂载启动请求。仅另补两个文件末尾换行，保留学习者其他格式改动。

新增 task-url.test.ts 13 条，Workspace 全量 396 条及聊天状态 84 条通过；TypeScript、全量 ESLint、启动器 Ruff、browser 脚本语法与 diff check 通过。隔离启动器补复制详情 GET 路由，workspace-task.mjs 扩展为 8 组，全部通过。

```bash
pnpm --dir apps/web typecheck
pnpm --dir apps/web lint
pnpm --dir apps/web test:workspaces
pnpm --dir apps/web test:state
.venv/bin/python -m ruff check apps/web/test/browser/run-isolated.py
BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=workspace-task.mjs \
PLAYWRIGHT_MODULE=/Users/wanxiancheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
.venv/bin/python apps/web/test/browser/run-isolated.py
```

真实 BFF/API/隔离 PostgreSQL 验证首发只创建一次且不中断、同会话续聊、刷新无需点击任务即可恢复、新标签页复制链接恢复；空草稿清除 URL、创建已提交但响应丢失、历史失败阻止发送及旧历史隔离继续通过。新增非法/不存在链接不回退草稿、详情重试后历史失败仍禁止发送、项目和任务均在第一页外直接恢复、项目列表失败不阻止详情恢复、忽略 abort 的旧详情不覆盖新草稿。React StrictMode 开启，控制台未出现水合或重复 key 错误。

1366×768、1920×1080、2560×1318 空白态检查及截图通过；1366/1920 恢复后聊天截图已视查，无横向溢出。截图位于 /private/tmp/agent-ui-preview/output/playwright/task-conversation-1366.png、task-conversation-1920.png 和 task-url-restored.png。聊天与标题模型出口模拟，不代表真实模型质量或历史运行摘要恢复已验收。

初次启动遇到本机数据库不可用，启动 Docker Desktop 后通过 docker compose -f infra/compose.yaml up -d --wait 恢复现有 PostgreSQL/Redis，保留命名卷，无开发库重置或迁移。浏览器结束后临时 BFF/API 与独立测试 schema/database 已自动清理；基础容器保持运行。


### 空任务删除事务验收（2026-09-16）

学习者完成 task_deletion_service.py；教练新增 tests/tasks/test_task_deletion_service.py 27 条。专项 27 passed（2.14s），Task 领域 116 passed（9.14s），均 -W error；领域 Ruff 和 diff check 通过。核心无需修正，仅补末尾换行。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -q tests/tasks/test_task_deletion_service.py
../../.venv/bin/python -W error -m pytest -q tests/tasks
../../.venv/bin/python -m ruff check app/services/tasks tests/tasks
```

复用根 conftest.py 的随机独立 PostgreSQL 数据库与私有 schema。覆盖真实提交后任务/会话消失、普通结果脱离 Session、重复删除、错误项目/归属/缺失会话、任意消息及 running/done/aborted/error/未知 Run 状态拒绝、运行事件保留、调用方已有事务不被回滚、结果构造/第二次 DELETE/提交前真实 SQL 故障整体回滚及 Session 可复用。

六个并发用例分别针对 Message/AgentRun：用 pg_blocking_pids 观察真实锁等待，删除先持锁时 INSERT 等待，删除提交后同一次 INSERT 得到外键错误 23503，删除回滚后 INSERT 成功；插入先持有外键父行锁时删除等待，插入提交后删除重新查询并拒绝。线程各用独立 Session，等待有超时且清理时释放阻塞。测试结束自动清理测试库/schema，不连接开发业务表，不运行迁移、浏览器或模型。

当前仅完成空任务删除服务；未新增 HTTP/BFF/UI，未宣称已经解决聊天自动重建会话或取消后协程清理问题。下一课收紧本地任务聊天的隐式会话创建，再继续删除闭环。


### 本地任务聊天禁止隐式重建会话（2026-09-16）

学习者修改 conversation_repository.py，在 local 模式下联查 Conversation/Task/Workspace 归属，并在原 get-or-create 的 INSERT 前返回已授权且锁定的会话。核心无需修正，仅补末尾换行。教练新增 tests/local/test_task_conversation_boundary.py 40 条，专项 40 passed（4.65s），受影响领域合计 189 passed（14.21s），-W error；Ruff、修改浏览器脚本 ESLint/语法及 diff check 通过。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -q tests/local/test_task_conversation_boundary.py
../../.venv/bin/python -W error -m pytest -q tests/local tests/tasks tests/chat/test_chat_api.py tests/chat/test_chat_stream.py tests/runtime/test_run_repository.py
../../.venv/bin/python -m ruff check app/repositories/chat/conversation_repository.py tests/local/test_task_conversation_boundary.py
```

真实 HTTP 验证普通/流式聊天对不存在、已删除、无 Task、会话/项目归属错配均返回安全 404，且有无旧缓存都不创建会话、Run、事件或调用模型；SQL 捕获确认无会话 INSERT。仓储 ensure/load/save 直接调用也拒绝；已有任务可多轮持久化并在清空缓存后恢复。模型调用期间另一个连接可 NOWAIT 锁会话，证明短授权事务已经结束。两个竞争测试通过 pg_blocking_pids 验证：删除先提交后 Run 创建拒绝且无重建；Run 先取得会话锁并提交后，删除等待并因 Run 存在拒绝。

真实 BFF/API/隔离 PostgreSQL 的 workspace-task.mjs first send 定向 1 组通过，覆盖首发、续聊、URL 刷新及新标签页恢复。local-mode.mjs 补齐先创建项目、打开草稿和展开运行详情的旧夹具前置步骤，3 组全部通过，包含无登录聊天、真实取消及项目基础流程。运行命令沿用上述隔离启动器：工作台设置 BROWSER_TEST_SCRIPT=workspace-task.mjs、BROWSER_SCENARIO='first send'；本地模式设置 BROWSER_TEST_SCRIPT=local-mode.mjs 并取消 BROWSER_SCENARIO。模型出口模拟，临时服务和随机测试库/schema 自动清理；未修改开发业务表或执行迁移。

本课不增加删除 HTTP/BFF/UI；下一课接空任务删除 HTTP。账号分支只保留兼容，未推进账号专项。普通非流式请求若已经开始调用模型，本课不自动取消它，但后续保存仍重新检查会话，不能重建已删除记录。

### 空任务删除 HTTP 与 BFF（2026-09-16）

学习者完成 DELETE HTTP；教练按本轮明确授权实现删除 BFF。成功返回 204 空正文，后端独立 Session 负责完整删除事务。BFF 仅转发服务端内部凭证及已校验 Origin；拒绝正文，按状态与错误码白名单返回安全文案。转发前取消与转发后结果未确认分开处理，取消/超时覆盖响应正文，不自动重试。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -q tests/tasks/test_task_delete_api.py
../../.venv/bin/python -W error -m pytest -q tests/tasks tests/workspace tests/local
../../.venv/bin/python -m ruff check app/routers/workspace app/services/tasks tests/tasks tests/workspace tests/local
cd ../web
pnpm test:workspaces
pnpm typecheck
pnpm lint
```

HTTP 专项新增 42 passed（4.19s），后端相关领域 422 passed（30.91s），-W error 与 Ruff 通过；BFF 新增 43 条，Workspace 全量 439 条通过；TypeScript、全量 ESLint 与 diff check 通过。HTTP 覆盖授权、来源、无正文、任意 Run 状态拒绝、事务中 SQL 失败回滚和提交后异常；BFF 覆盖 204、状态/错误码匹配、脱敏、取消、超时、正文读取与不重试。

仓库根目录运行真实浏览器→BFF→API→隔离 PostgreSQL 定向验收：

```bash
BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=workspace-task.mjs BROWSER_SCENARIO='delete BFF' \
PLAYWRIGHT_MODULE=/Users/wanxiancheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
.venv/bin/python apps/web/test/browser/run-isolated.py
```

1 组通过：浏览器同源 DELETE 空任务返回 204/空正文/no-store，重复删除和详情读取 404，列表为空；正常发送消息后删除返回 409，任务仍可读取，刷新恢复消息。PC 1366×768，模型出口模拟，无真实模型费用；临时服务与随机独立数据库/schema 已自动清理，未操作开发业务表。当前没有删除 UI，下一课接侧栏入口、确认、防重及删除后选中状态恢复。

### 侧栏删除 UI、项目菜单与加载体验（2026-09-16）

学习者完成删除状态、ref 防重、204/409/404/结果未确认处理、详情查询与选中状态清理；教练最初仅修正两处缩进。之后按用户明确要求和两张 Codex 截图，调整任务行悬停删除图标、Radix 确认框及项目省略号菜单；菜单接入新建任务、项目设置和刷新任务，不增加未实现的归档/置顶/移除项目。

按用户要求修复加载闪烁：将 WorkbenchShell 移出 keyed TaskChat，导航不随会话切换重建；通过 Portal 将当前任务详情放入固定右栏。ProjectGroup 以项目标识为稳定 key，保留展开状态与已有列表；revision 负责后台刷新并将分页请求重置到第一页，确认删除后立即排除旧列表目标。历史查询仍真实执行且阻止提前发送；250ms 后才显示静态、带无障碍标签的骨架，不引入跨任务消息缓存。

```bash
cd apps/web
pnpm typecheck
pnpm lint
pnpm test:workspaces
pnpm test:state
```

Workspace 439 条、聊天状态 84 条通过，TypeScript 与全量 ESLint 通过。新增 task-delete.mjs 共 10 个场景：删除/菜单 9 组已通过，加载保持 1 组在 Shell 调整后定向通过。最后针对布局影响重复运行删除、菜单与既有聊天流程，命令如下（从仓库根目录运行）：

```bash
BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=workbench-regression.mjs \
BROWSER_SCENARIO='project menu,delete UI confirm,delete UI late,delete UI cancels,first send,new draft,history failure' \
PLAYWRIGHT_MODULE=/Users/wanxiancheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
.venv/bin/python apps/web/test/browser/run-isolated.py
```

单跑新增完整场景时设置 BROWSER_TEST_SCRIPT=task-delete.mjs 并移除 BROWSER_SCENARIO；定向加载体验设置 BROWSER_SCENARIO='loading UX'。workbench-regression.mjs 依次运行新旧脚本，共用一轮临时服务，各自关闭浏览器。所有数据库操作均为自动生成的隔离 PostgreSQL 数据库/schema；模型出口模拟，无开发业务表改动，也未运行开发库迁移。

覆盖：确认取消和键盘焦点、真实空任务删除和历史任务拒绝、同一事件循环双击、刷新/收起侧栏时操作不丢、迟到响应不覆盖任务或草稿、提交后响应丢失、详情查询现存/畸形/异常/404、恢复详情期间删除、项目菜单三个操作、列表 DOM 保持与慢历史骨架。首次测试将已有 Task 的首发错误地当作草稿创建并期待自动标题，已修正测试前提；菜单测试以实际 Radix 无障碍角色定位。加载回归发现父级 TaskChat 销毁整个 Shell 后，修正布局边界再验收，未用隐藏文案掩盖组件重建。

截图位于 /private/tmp/agent-ui-preview/output/playwright/：delete-ui-hover.png、delete-ui-confirm.png、project-actions-menu.png、project-actions-menu-dark.png、history-loading-placeholder.png。默认 1366×768，另检查 1920×1080；重要界面已人工视查。

已知边界：仅支持空任务；详情 200 不证明先前 DELETE 已停止，因此仍保留未确认态。刷新页面会丢失客户端操作提示；服务端幂等和最终操作记录仍待后续课程。

最终结果：Shell 外移后，删除/项目菜单定向 5 组和既有聊天主流程 3 组全部通过；加上加载体验定向 1 组，验证列表节点在新建/切换/后台刷新时保持同一 DOM，历史完成前禁止发送。新脚本共 10 个场景已分别验收通过。浅/深色菜单、悬停删除、确认框和历史骨架截图已视查；临时服务及独立 PostgreSQL 数据库/schema 已清理。最终 TypeScript、全量 ESLint、Workspace 439 条、聊天状态 84 条与 git diff --check 通过；改动未提交。

### 任务运行历史只读查询（2026-09-16）

学习者完成 apps/api/app/services/tasks/task_run_query.py 与 TaskRunItemResponse/TaskRunListResponse。核心无需修正，仅补文件末尾换行。新增 tests/tasks/test_task_run_query.py 44 条专项通过（2.67s）；Task/runtime/local/workspace 相关领域 560 条通过（38.31s），启用 -W error；后端全量 Ruff 与 git diff --check 通过。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -q tests/tasks/test_task_run_query.py
../../.venv/bin/python -W error -m pytest -q tests/tasks tests/runtime tests/local tests/workspace
../../.venv/bin/python -m ruff check app tests
```

复用根 conftest.py 创建随机独立 PostgreSQL 数据库/schema，真实提交准备数据并自动清理。覆盖空任务也先授权、错误项目与会话归属、分页上下界、bool/字符串拒绝、多页无漏项/重复、游标记录删除后继续翻页、新记录只在刷新第一页出现、未知状态与最终耗时、兄弟会话隔离。SQL 记录断言只有 SELECT、不读取事件、不加行锁；服务 Session.commit 被禁止，成功与实际 PostgreSQL 除零错误、响应校验失败均验证 Session 关闭。

本课没有增加 HTTP/BFF/UI，没有执行开发库迁移、浏览器或模型调用。下一课接运行列表 HTTP；仅以 Run ID 定义顺序，不宣称数据库快照或严格提交时间排序。改动未提交。

### 任务运行历史 HTTP（2026-09-16）

学习者完成 GET /workspaces/{workspace_id}/tasks/{task_id}/runs。核心无需修改；新增 tests/tasks/test_task_run_api.py 42 条专项通过（4.53s），受影响 Task/runtime/local/workspace 共 602 条通过（44.08s）。全量后端 Ruff 与 git diff --check 通过。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -q --tb=short tests/tasks/test_task_run_api.py
../../.venv/bin/python -W error -m pytest -q --tb=short tests/tasks tests/runtime tests/local tests/workspace
../../.venv/bin/python -m ruff check app tests
```

使用真实 FastAPI ASGI 请求和 PostgreSQL 隔离库/schema，允许夹具真实提交并自动清理。首次执行因沙箱禁止连接 127.0.0.1:5432 失败，获得执行权限后通过；不属于应用测试失败。覆盖分页、概要字段、空结果授权、项目/会话归属、422 参数错误、本地边界先于身份、SQL 和响应结构异常脱敏、no-store 及 OpenAPI。未触碰开发业务表、未执行迁移或模型调用；BFF/UI 留待下一课。

### 任务运行历史 BFF（2026-09-16）

用户明确授权本课直接实现。新增 task-run-data.ts、task-run-proxy.ts 与 GET /api/workspaces/[workspaceId]/tasks/[taskId]/runs；79 条专项与 Workspace 全量 518 条通过，TypeScript、ESLint、diff check 通过。

```bash
cd apps/web
node --experimental-strip-types --test test/features/workspaces/task-run-route.test.ts
pnpm test:workspaces
pnpm typecheck
pnpm lint
```

测试直接调用实际 GET 路由，模拟上游 fetch 与超时信号；覆盖本地边界、凭证隔离、分页参数、资源匹配、倒序/游标、概要字段重建、异常脱敏、请求/正文/JSON 完成阶段取消超时。不代表浏览器或真实 BFF/API 网络联调；本课没有 UI 改动、数据库操作和模型调用。

### 运行历史列表 UI（2026-09-16）

学习者完成 task-run-history.tsx 及详情区接入。教练恢复被替换掉的 RunSummaryCard、补末尾换行，并在隔离启动器中补复制 runs 路由。新增 task-run-history.mjs，最终 5 组场景通过：真实 BFF/API 空列表、分页失败保留与原游标重试、刷新替换/失败保留及快请求无骨架、慢请求骨架/切换旧响应隔离、重新展开读取第一页。分页及故障通过浏览器拦截模拟，不宣称真实数据库多页运行联调；无真实模型调用。

```bash
BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=task-run-history.mjs \
PLAYWRIGHT_MODULE=/Users/wanxiancheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
.venv/bin/python apps/web/test/browser/run-isolated.py
```

首次夹具未复制新增路由导致 404，修正后通过；React Strict Mode 可能重跑 effect，重开断言检查第一页语义而非恰好一次请求。1366/1920×900 截图已视查（/private/tmp/agent-ui-preview/output/playwright/run-history-*.png）。Workspace 518、聊天状态 84 条通过，pnpm typecheck、pnpm lint、夹具 Ruff、git diff --check 通过。临时服务与独立 PostgreSQL 库/schema 已自动清理；没有开发库迁移。

### 运行详情读取 BFF（2026-09-16）

按用户明确授权新增 GET /api/runs/[runId]、run-detail-proxy.ts、run-detail-data.ts。新增 run-detail-route.test.ts 61 条（包含在 Workspace 全量 579 条中）通过，聊天状态 84 条、TypeScript、ESLint、夹具 Ruff 和 diff check 通过。

```bash
cd apps/web
pnpm typecheck
pnpm lint
pnpm test:workspaces
pnpm test:state
```

直接调用真实 GET 路由，上游 fetch 模拟；覆盖本地凭证边界、规范 Run ID、未知查询参数、错误脱敏、请求/正文/JSON 完成阶段取消超时、事件顺序与公开字段、取消型 RUN_ERROR、运行指标嵌套重建。未知事件保留 id/type/time，payload 返回空对象。此次未运行浏览器、数据库或模型，隔离启动器已补复制新路由供后续 UI 验收。详情按 Run 授权，不声称提供当前 Task 的关联证明。

### 历史详情 UI（2026-09-16）

按用户明确授权完成 TaskRunPanel、TaskRunHistory 选择回调和 ChatPanel 接入。扩展 task-run-history.mjs，原列表 5 组与详情 3 组共 8 组通过；详情覆盖错误重试、未知 payload 过滤、HTML 文本显示、草稿和列表节点保留、返回/切换任务迟到结果隔离、空事件、收起重开回列表。命令沿用上一节运行历史 UI 的隔离启动器。

详情/分页/故障通过浏览器路由模拟，空列表为真实 BFF/API/隔离 PostgreSQL；无真实模型调用。1366/1920×900 截图已视查，路径 /private/tmp/agent-ui-preview/output/playwright/run-detail-*.png。pnpm typecheck、pnpm lint、Workspace 579 条、聊天状态 84 条及 git diff --check 通过。临时服务与测试库/schema 自动清理。

### 启动迁移职责收口（2026-09-16）

API lifespan 只调用 check_database_ready，不再自动建表。数据库当前 heads 必须与 apps/api/migrations 的 heads 一致；检查不 commit、不写版本，空库/旧版本/未知版本拒绝启动。版本一致不等同于逐列结构验证。显式迁移后启动：

```bash
cd apps/api
../../.venv/bin/python -m alembic upgrade head
../../.venv/bin/python -m uvicorn app.main:app --reload
```

此次没有对开发库执行迁移；只读就绪检查已通过。tests/migrations/test_database_readiness.py 新增 7 条通过；后端全量 1101 条通过（79.95s，-W error），Ruff 与 diff check 通过。Alembic env 支持 config.attributes.connection，用于隔离测试的真实迁移；浏览器夹具已改为从空 schema 执行 upgrade head 后再种测试数据和启动 API。

### 右侧栏调宽与摘要布局（2026-09-16）

右侧栏默认 400px，最小 320px、最大 720px，并根据容器宽度及左栏状态为中栏预留 400px。拖动左边界调宽，方向键每次 16px、Shift 48px，Home/End 到边界，双击恢复 400px；localStorage 的 agent-workbench-details-width 保存用户偏好。改变宽度不重挂载聊天或详情。窄屏空间不足时仍优先保持右栏 320px；本产品验收为 PC 场景。

摘要从屏幕断点控制的三列卡片改成标签/数值行，数值不拆行；当前运行取消重复卡片边框。task-run-history.mjs 共 9 组通过，新增真实聊天/API/隔离 PostgreSQL 场景，模型出口模拟；另替换流指标为 757 Token、¥0.00128420、1499 ms 复现用户截图。断言 320px 摘要不溢出、拖动和键盘改变宽度、草稿与 textarea 节点保持、刷新后恢复宽度。1366×900 窄/宽截图已视查：/private/tmp/agent-ui-preview/output/playwright/details-narrow.png 与 details-wide.png。

沿用 BROWSER_TEST_SCRIPT=task-run-history.mjs 的启动命令；测试库由真实 Alembic upgrade 创建，临时服务和隔离库/schema 自动清理。前端 pnpm typecheck、全量 lint、Workspace 579 条、聊天状态 84 条通过；新脚本 lint、后端全量 Ruff 与 git diff --check 通过。

### Markdown 回复展示（2026-09-16）

新增 MarkdownMessage 复用 react-markdown 10.1.0 与 remark-gfm 4.0.1，依赖与 pnpm-lock.yaml 同步。历史助手消息和流式回复使用相同渲染，用户消息保持纯文本；不修改存储和流协议。原始 HTML 不执行，危险协议由库默认 URL 变换过滤；远程图片提供显式打开入口，不自动加载。

浏览器 markdown-message.mjs 三组通过：模拟分块流的中间态、格式/安全链接/HTML/代码内部滚动、刷新后历史 Markdown 与用户原文。通过真实 BFF 创建隔离项目/任务，回复和历史内容模拟；没有真实模型调用。启动命令沿用隔离启动器，设置 BROWSER_TEST_SCRIPT=markdown-message.mjs。1366×900 浅/深色截图已视查：/private/tmp/agent-ui-preview/output/playwright/markdown-light.png、markdown-dark.png。TypeScript、全量 ESLint、聊天状态84条、Workspace579条及 diff check 通过；临时服务和测试库/schema 已清理。

### 2026-09-16：Task 创建请求模型与迁移验收

- 修正模型表名为 `task_creation_requests`、主键参数为 `primary_key`；迁移核心无需修改。
- 新增 `tests/migrations/test_task_creation_request_migration.py` 17 条，整个迁移目录 43 条通过（5.92s，`-W error`）。使用根夹具自动清理的隔离 PostgreSQL 库/schema；验证升级/回退/再升级保留旧数据、约束、ORM 与真实删除服务后的 SET NULL/请求键保留。
- 旧迁移测试比较对应版本的模型快照，排除后续新表，避免用当前 head 要求历史版本。
- 在 `apps/api` 执行：`../../.venv/bin/python -W error -m pytest -q --tb=short tests/migrations`。
- 开发库先以 `alembic current` 确认为 `f16a53d928bc`，再执行 `../../.venv/bin/python -m alembic upgrade head` 升级至 `0a7b64e039cd`；`alembic check` 无新增结构操作。开发库未执行回退，新增表不回填历史任务。
- 当前仅完成存储基础，HTTP/BFF/UI 尚未提供创建幂等。未调用真实模型或运行浏览器测试。
- 全量回归：在 `apps/api` 执行 `../../.venv/bin/python -W error -m pytest -q --tb=short`，1118 passed（75.03s）；`../../.venv/bin/python -m ruff check app tests` 与 `git diff --check` 通过。
