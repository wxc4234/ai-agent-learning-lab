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


### 2026-09-17：Task 创建幂等事务服务验收

学习者完成 task_service.py 核心；教练仅将参考中的嵌套 if 等价合并以通过 Ruff，并补末尾换行。新增 tests/tasks/test_task_creation_idempotency.py 37 条，复用根 PostgreSQL 独立库/schema 夹具与已有项目夹具。覆盖规范化重放、严格键格式、作用域、冲突、当前标题与原指纹、授权先于请求查询、资源归属重查、删除后保留键、SQL 故障整体回滚、提交确认丢失后的重试、旧身份映射刷新，以及创建/删除提交和回滚的真实锁等待。

从 apps/api 执行：

```bash
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/tasks/test_task_creation_idempotency.py
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/tasks tests/workspace tests/local
../../.venv/bin/python -m ruff check app tests
```

首批专项 34 passed（2.47s）；增加确认丢失和旧 Session 缓存 3 条后，全部 37 条随相关回归共 545 passed（41.45s，-W error）。后端全量 Ruff 与 git diff --check 通过。并发场景使用 pg_blocking_pids 确認数据库真实等待后才放行事务，不用固定睡眠推断竞争结果。独立测试库/schema 已自动清理。

环境恢复：首次沙箱禁止本机 TCP；受控权限重跑后确认 PostgreSQL 拒绝连接，Docker daemon 未启动。启动 Docker Desktop 并执行 docker compose -f infra/compose.yaml up -d --wait postgres redis 后两者健康，才完成以上验收。未迁移或改动开发业务表，未调用模型。当前 HTTP 仍不传 request_key，因此本轮不宣称浏览器创建已幂等；未运行前端/浏览器或后端全量回归。


### 2026-09-17：Task 创建幂等 HTTP 接入验收

学习者完成 schemas.py 和 workspace.py 的请求键校验/转交与安全异常映射，核心与参考一致，无需修正。新增 tests/tasks/test_task_idempotency_api.py 37 条真实 app/隔离 PostgreSQL 测试，专项 37 passed（3.83s）；Task/Workspace/local 相关回归 582 passed（42.15s，-W error）。后端全量 Ruff 与 git diff --check 通过。

从 apps/api 执行：

```bash
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/tasks/test_task_idempotency_api.py
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/tasks tests/workspace tests/local
../../.venv/bin/python -m ruff check app tests
```

覆盖真实同键重放/内容冲突/删除后拒绝、新项目作用域、缺省与 null 兼容、非法键/额外可信字段拒绝、服务异常脱敏、Host/Origin/内部凭证/本地模式边界、公开字段及 no-store。SQL 写入失败整体回滚；提交后结果构造、非法服务结果、实际 response_model 校验及确认异常返回 500 后，同键重试返回已存标识，独立连接确认仅一组三条记录。

使用根夹具随机独立测试库与私有 schema，正常结束并自动清理；没有访问开发业务表或执行迁移。未改 BFF/UI，未运行前端、浏览器或模型；未重复账号专项或后端全量。HTTP 暂允许缺省/null 键，成功创建与重放均 201；现有 BFF 仍仅发送 title，浏览器尚无创建幂等保证。


### 2026-09-17：Task 创建幂等 BFF 接入验收

按本课明确授权修改现有 tasks/route.ts：只允许 title 与可选 request_key，缺省/null 兼容旧 UI；合法键严格为 32 位小写十六进制并原样转发，不生成、规范化、更换或自动重试。显式长度检查拒绝 JavaScript 正则 $ 可匹配的尾换行。新增两种 409 和非法键 422 白名单，状态与错误码须同时匹配；不反射上游 message 或复制内部字段/响应头。

创建路由 task-create-route.test.ts 新增 33 条，总计 92 条通过；既有取消矩阵补带键请求验证，覆盖转发前、请求 JSON、fetch、响应 JSON 及解析完成后的取消/超时。覆盖合法/null/缺省键、畸形/尾换行、额外可信字段、状态错配/原型键，以及网络/JSON/服务器失败后不自动重试，调用方再次调用时复用相同输入与键。

从 apps/web 执行：

```bash
node --experimental-strip-types --test test/features/workspaces/task-create-route.test.ts
pnpm test:workspaces
pnpm typecheck
pnpm lint
```

结果：创建路由 92 条、Workspace 全量 612 条通过，TypeScript、全量 ESLint 和 git diff --check 通过。使用真实路由 Request/Response，上游 fetch 模拟；未运行浏览器、数据库或模型。Next.js 仍提示本机 Node 经 Rosetta 运行，不影响本轮检查通过。UI 尚未持有请求键，因此不能宣称现有浏览器创建流程已经幂等。

### 2026-09-17：Task 创建幂等 UI 接入

用户明确要求教练直接完成并说明实现。修改 workbench-session.tsx 和 chat-panel.tsx：首次创建意图生成去掉连字符的 UUID v4 请求键，Provider 按项目保留草稿 key、原始标题和正文；切换项目后回到草稿仍能重试。同步 ref 防双提交；卸载取消及 mounted 检查隔离旧结果，超时覆盖正文解析。未知结果保留同键和输入，409 冲突/删除拒绝不自动换键；明确开始另一任务才清除意图。首次成功沿用自动首发，重试成功只找回任务、写 URL、读取历史，等待用户明确发送。

新增 task-create-idempotency.mjs 4 组 PC 浏览器测试通过：真实 BFF/API/隔离 PostgreSQL 提交后故意丢响应、项目切换后同键重放和双击、两种 409 后明确新意图换键、重复 504 仍保留原键且不发送消息。冲突及 504 响应模拟，模型出口沿用隔离替身。原 workspace-task.mjs 仅同步未确认提示文案。

从仓库根目录运行：

```bash
BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=task-create-idempotency.mjs \
PLAYWRIGHT_MODULE=/Users/wanxiancheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
.venv/bin/python apps/web/test/browser/run-isolated.py
```

可添加 CREATION_REGRESSION=1 和 BROWSER_SCENARIO='committed,first send,new draft,history failure'，在同轮隔离服务补跑首发/项目切换/列表找回/历史失败与旧请求隔离。截图位于 /private/tmp/agent-ui-preview/output/playwright/creation-uncertain-1366.png 和 creation-recovered-1920.png，浅深色截图已视查；视查后修正找回提示颜色及发送后清除。

前端 pnpm test:workspaces 612 条、pnpm test:state 84 条、pnpm typecheck 和全量 pnpm lint 通过。测试夹具运行真实迁移，结束自动清理临时服务和独立库/schema；未修改开发业务表，不重复后端全量测试。创建意图仅存页面内存，不写 localStorage/sessionStorage；刷新或关闭会丢失重试键与未发送内容，UI 已明确提示，不能宣称跨刷新恢复或聊天消息幂等。

最终回归：提示修正后关键恢复场景再跑 1 组通过；既有首发/同会话续聊与刷新恢复、项目切换、从列表找回、历史失败与迟到结果隔离共 4 组全部通过。两轮隔离服务及 PostgreSQL 库/schema 均正常清理，git diff --check 通过。


### 2026-09-17：会话执行占用模型与迁移验收

学习者完成 ConversationExecutionSlot 和 revision 1b8c75f140de，核心与参考一致，仅补迁移末尾换行。新增 tests/migrations/test_conversation_execution_slot_migration.py 13 条；历史 Task/Workspace 迁移测试的模型快照排除后续新增表。

从 apps/api 执行：

```bash
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/migrations
../../.venv/bin/python -W error -m pytest -xq --tb=short
../../.venv/bin/python -m ruff check app tests migrations
../../.venv/bin/python -m alembic current
../../.venv/bin/python -m alembic upgrade head
../../.venv/bin/python -m alembic current
../../.venv/bin/python -m alembic check
```

结果：迁移目录56 passed（7.42s），后端全量1205 passed（82.51s，-W error），Ruff/diff check通过。覆盖真实升级/回退/再升级与旧 Task/Conversation/Run 数据保留、历史 Run 不回填、主键唯一、格式/非空/外键、ORM 提交与带时区时间、禁止静默删除会话、真实空任务删除失败的整体回滚。

隔离测试库/schema 已清理。测试全部通过后，开发库由 0a7b64e039cd 升级至 1b8c75f140de (head)，alembic check 返回 No new upgrade operations detected；仅新增占用表，不删除或回填业务数据。回退只在隔离测试库执行。本课没有接入运行流程，也未运行前端或浏览器；不能宣称已经拦截并发，崩溃遗留占用恢复仍待后续设计。


### 2026-09-17：会话执行占用获取与释放事务服务验收

学习者完成 app/services/runtime/execution/conversation_execution_service.py，核心与参考一致，无需修正。教练新增 tests/runtime/execution/test_conversation_execution_service.py 共 42 条专项，使用公共 PostgreSQL 隔离夹具。

从 apps/api 执行：

```bash
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime tests/tasks tests/local
../../.venv/bin/python -m ruff check app tests
```

首批专项 40 passed（2.89s）；补齐返回值构造失败回滚、陈旧占用与取消终态不自动放行后，最终 42 条专项随相关回归共 508 passed（36.91s，-W error）。Ruff 与仓库根目录 git diff --check 通过。覆盖本地归属与先授权、精确释放/重复释放/迟到释放、拒绝已有事务且不影响调用方工作、真实 SQL 与提交前后故障、Session 可复用。并发测试使用 pg_blocking_pids 确认真正等待行锁，分别验证获取/释放提交或回滚后的竞争结果，以及不同会话可独立获取。

隔离数据库/schema 随夹具清理；本课未修改数据库结构或开发业务表，未运行前端、浏览器或真实模型调用。服务尚未接入聊天运行，不能宣称已拦截页面端并发；取消终态、占用时间和等待协程取消均不能自动触发释放，进程崩溃遗留占用恢复仍待后续设计。


### 2026-09-18：执行后台线程跟踪器验收

学习者完成 app/services/runtime/execution/execution_threads.py，核心与参考一致，仅补末尾换行。教练新增 tests/runtime/execution/test_execution_threads.py 共 12 条，使用真实线程和 Event 主动控制开始/结束，不通过固定毫秒延时猜测线程是否完成；线程等待设置故障保险，finally 放行线程，避免失败时遗留阻塞。

从 apps/api 执行：

```bash
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/execution/test_execution_threads.py
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/execution/test_execution_threads.py tests/runtime/agent/test_agent_runtime.py
../../.venv/bin/python -m ruff check app tests
```

结果：新增专项 12 passed（0.04s），相关回归 28 passed（0.82s），Ruff 与根目录 git diff --check 通过。覆盖参数/返回值与 ContextVar 在线程中传递、原异常传播、空集合/重复关闭、关闭后拒绝工作、调用方取消/超时后线程继续并被等待、放弃等待后的线程成功/失败、重复取消关闭者、多个关闭者隔离、失败任务不跳过其他任务、不同实例独立。

本课没有数据库、模型或浏览器调用，未改聊天入口和现有工具执行路径。尚不能宣称实际会话占用已安全释放；ASGI/AnyIO 取消域、流未开始/关闭和应用停机等集成边界留待后续验证。该组件不提供强制终止线程的能力；同步函数不返回时，正常收尾会继续等待。


### 2026-09-18：会话执行作用域验收

学习者完成 conversation_execution_scope.py，按对话纠正为 AsyncGenerator[ExecutionThreads, None] 标注；不是 asynccontextmanager 本身弃用。核心行为无需修正，教练仅补换行及预期异常捕获的 BLE001 豁免说明。新增 tests/runtime/execution/test_conversation_execution_scope.py，复用已有本地任务夹具及根 conftest 的隔离 PostgreSQL 数据库/schema。

从 apps/api 执行：

```bash
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/execution/test_conversation_execution_scope.py
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime
../../.venv/bin/python -m ruff check app tests
```

结果：新增专项 17 passed（1.56s），Runtime 165 passed（12.45s），Ruff 与 git diff --check 通过。覆盖正常/业务异常/取消后的释放、忙与越权不执行业务、获取前后暂停时取消后清理成功占用、后台线程成功/失败及重复取消、AnyIO 取消域、释放期间取消后等待真实提交、获取/释放提交确认丢失的数据库事实、替换持有者后旧释放拒绝。

初次运行因 Docker Desktop 未启动而 PostgreSQL 拒绝连接；启动 Docker 并执行 docker compose -f infra/compose.yaml up -d --wait postgres redis 后容器健康，重跑通过。保留已有数据卷，未迁移或修改开发业务表；独立测试资源自动清理，无真实模型、前端或浏览器调用。仅验收独立执行作用域，实际 ASGI 响应、工具与聊天入口仍待接入和验证。


### 2026-09-18：Agent Loop 工具线程接入验收

学习者完成 agent_runtime.py 的可选 execution_threads 参数、工具执行转交以及 run_agent_loop 透传，核心无需修改。教练新增 tests/runtime/agent/test_agent_execution_threads.py 共 12 条；两种 Runtime 调用路径覆盖成功、失败、超时与取消，注册/参数校验拒绝时不进入跟踪器；成功后跟踪器仍可登记工作，由外层负责关闭。

从 apps/api 执行：

```bash
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime
../../.venv/bin/python -m ruff check app tests
```

结果：Runtime 177 passed（13.70s，包含新增 12 条），Ruff 与仓库根 git diff --check 通过。既有未传跟踪器的兼容测试同步通过。两个独立 PostgreSQL 集成场景串联真实工具、Runtime、执行作用域和占用事务：工具超时或运行取消后，线程仍在执行时占用保留，线程结束后才释放。线程由事件控制和 finally 放行，测试数据库/schema 自动清理；没有修改开发业务表或调用真实模型。

真实聊天入口仍未传入跟踪器，也未持有执行作用域；本课未做前端或浏览器验收，不能宣称页面端并发已经受保护。工具超时是停止等待而非强制终止线程；同一轮超时后的后续工具调度仍沿用原协议，本课没有实现工具进程隔离或停止旧工具。


### 2026-09-18：普通/流式聊天入口执行占用接入

用户明确要求教练直接实现。本课新增 routers/chat/chat_execution.py，两个入口通过 Depends(scope="request") 共用执行作用域；取得占用后才创建 Run 或执行业务。生成器、取消监听器、Run 创建任务均由请求持有；响应中断或流未开始也受保护地收尾，后台数据库与工具线程全部结束后才释放。缓存每轮重新读取并在退出时失效，兜底 finish_agent_run 不覆盖已有终态。API/BFF 和聊天状态层接通安全 409。

从 apps/api 执行：

```bash
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/chat/test_chat_execution_lifecycle.py
../../.venv/bin/python -W error -m pytest -xq --tb=short
../../.venv/bin/python -m ruff check app tests
```

新增 ASGI/PostgreSQL 专项 12 passed（1.62s），后端全量 1300 passed（99.38s，-W error），Ruff/diff check 通过。覆盖普通/流式交叉竞争、不同会话独立、忙请求无 Run/消息/模型副作用、ASGI 2.0/2.4 发送失败、响应头失败时生成器未开始、Run 创建与消息提交阶段反复取消、真实工具工作期间断线与占用延迟释放。

从 apps/web 执行：

```bash
node --experimental-strip-types --test test/features/auth/chat-stream-route.test.ts
pnpm test:state
pnpm typecheck
pnpm lint
```

结果：BFF 37 条（新增2条）、聊天状态85条（新增409场景）、TypeScript/ESLint 通过。原 run-terminal 测试夹具未提供此前加入的 setCreationError，已补无副作用 setter 后重跑通过。服务级流测试夹具补齐线程/监听器生命周期；认证边界测试继续用显式替身，真实资源边界由隔离 PostgreSQL 集成覆盖。

浏览器使用本地主线、真实 BFF/API/隔离 PostgreSQL 与模拟模型，脚本 apps/web/test/browser/chat-execution.mjs。运行命令：

```bash
BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=chat-execution.mjs \
PLAYWRIGHT_MODULE=/Users/wanxiancheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
.venv/bin/python apps/web/test/browser/run-isolated.py
```

最终 PC 浏览器两组场景全部通过：并发标签页收到真实409并显示忙提示；取消后占用释放、再次发送成功并在刷新后恢复持久消息。1366×900 忙提示与1920×900恢复截图已视查；隔离服务和数据库/schema均已清理。截图位于 /private/tmp/agent-ui-preview/output/playwright/execution-busy-1366.png 和 execution-recovered-1920.png。

测试中的 API 重试仅用于观察占用最终释放，不是产品新增自动重试。初次浏览器运行因后台标签页初始化及折叠详情状态文案的可见性等待失败，脚本已补前台激活、等待历史恢复和状态节点存在检查。

边界：不修改数据库结构或开发业务表，无真实模型调用。进程崩溃、占用获取提交确认丢失后的恢复、Run 创建已提交但未返回 ID 的诊断仍待后续实现；不保证强制停止线程，也不把终态当作线程已停止。


### 2026-09-18：会话执行占用只读查询服务

学习者完成 services/runtime/execution/conversation_execution_query.py，核心无需修改，教练仅补末尾换行；新增 tests/runtime/execution/test_conversation_execution_query.py 共 12 条。复用公共 PostgreSQL 隔离数据库/私有 schema 和本地任务夹具，允许真实提交，结束自动清理；未使用开发业务表。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime
../../.venv/bin/python -m ruff check app tests
```

结果：Runtime 189 passed（13.96s，-W error）；Ruff 与 git diff --check 通过。覆盖获取/释放前后快照、关闭 Session 后结果可用、不可变公开字段、五类授权拒绝且不查询占用表、四类 Run 状态与旧占用独立、不同会话隔离，以及授权/占用查询阶段的数据库异常传播与事务结束。监听真实 SQL 验证仅 SELECT、无 owner_token/Run 读取/行锁及无提交，并独立查询确认记录未变。数据库错误由 SQL 执行钩子注入，没有模拟 PostgreSQL 查询结果。

本课没有 HTTP/UI 变更，未重复浏览器或前端回归。只读是本服务不写入的行为约束，并未设置 PostgreSQL READ ONLY 事务；状态只是查询快照，不证明进程存活，也不是下一次发送的授权或占用许可。未验证自动恢复、强制停机或占用过期。


### 2026-09-20：会话执行占用状态查询 HTTP

学习者完成 schemas.py 公开响应与 conversation.py GET /sessions/{session_id}/execution，核心无需修正；教练只补定义间空行。新增 tests/chat/test_conversation_execution_api.py 共 13 条，使用真实主应用、本地身份依赖、公共随机 PostgreSQL 数据库/私有 schema，结束自动清理。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/chat/test_conversation_execution_api.py tests/runtime/execution/test_conversation_execution_query.py tests/local/test_local_mode.py tests/local/test_task_conversation_boundary.py tests/chat/test_chat_execution_lifecycle.py
../../.venv/bin/python -m ruff check app tests
```

结果：90 passed（7.70s，-W error），Ruff 与 git diff --check 通过。专项覆盖三字段/时区/null/OpenAPI 契约、占用查询 200、释放后空闲、四类会话授权拒绝且不读占用表、四类本地访问拦截、伪造 user_id 无效、线程执行同步查询、真实 SQL 故障安全 500、旧占用与 aborted Run 及无占用与 running Run 的独立语义。独立连接核对会话/消息/Run/事件数量与占用记录不变；成功与错误响应均 no-store。

本地身份依赖仍可能初始化本机用户，不能声称整个 HTTP 请求没有数据库写入。占用查询不创建 Run、不读取 token、不释放占用，数据库异常不能变成空闲。首次测试在建库前因 127.0.0.1:5432 拒绝连接而中止；确认 Docker 已运行，执行 docker compose -f infra/compose.yaml up -d --wait postgres redis，服务健康后重跑成功。保留数据卷，未修改开发业务表或执行迁移。此课没有前端改动，未跑浏览器/前端或真实模型。


### 2026-09-20：会话执行占用状态查询 BFF

学习者完成 conversation-execution-data.ts、conversation-execution-proxy.ts 和 sessions/[sessionId]/execution/route.ts，核心无需修正，教练仅补末尾换行。新增 test/features/workspaces/conversation-execution-route.test.ts 共 60 条，通过导出的 GET 路由测试真实解析器/代理，上游 fetch 模拟。

```bash
cd apps/web
node --experimental-strip-types --test test/features/workspaces/conversation-execution-route.test.ts
pnpm test:workspaces
pnpm typecheck
pnpm lint
```

专项 60/60 通过（0.76s），Workspace 全量 672/672 通过（1.79s）；Next 类型生成/TypeScript、ESLint、git diff --check 通过。覆盖本地凭证与浏览器 Cookie/Authorization 隔离、内部字段/响应头剥离、路径及查询参数拒绝、状态/时间配对与会话 ID 校验、微秒与时区时间、陈旧占用保留、错误状态白名单、不读错误正文、正文取消失败兜底、网络失败不重试、客户端取消/20 秒超时在请求及正文阶段生效。超时使用受控信号驱动，断言生产预算，不等待真实 20 秒。

本课仅模拟上游验证 BFF，没有启动真实后端、访问数据库或运行浏览器，不宣称查询 UI 已完成。类型检查提示当前 Node 在 Apple Silicon 上使用 Rosetta 转译，但命令成功；未因此更换运行环境。查询 BFF 仅支持本地主线，不新增账号模式查询支持。


### 2026-09-20：会话执行占用只读 UI

学习者完成 conversation-execution-panel.tsx 与 chat-panel.tsx 右侧接入；核心无需修改，仅补新文件末尾换行。教练新增 conversation-execution-panel.test.ts 8 条，通过 AST 定位并执行组件真实 refresh 函数，验证重复点击、网络/JSON/契约/HTTP 失败重试、请求/正文超时及旧请求不影响新引用。Workspace 共 680 条通过（2.56s），聊天状态85条通过（2.12s）；pnpm typecheck、pnpm lint 与 git diff --check 通过。无新核心行为修改。

浏览器启动器补入新 sessions/[sessionId]/execution BFF 路由复制，使用既有随机独立 PostgreSQL 库/schema 与迁移链，真实 API/BFF，模拟模型。运行：

```bash
BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=conversation-execution.mjs \
PLAYWRIGHT_MODULE=/Users/wanxiancheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
.venv/bin/python apps/web/test/browser/run-isolated.py
```

5 组全部通过：键盘手动查询与真实空闲/占用展示；另一标签页取消后真实释放、手动刷新恢复空闲；HTTP/契约错误显示未知且重试恢复；任务切换取消旧请求并拒绝故意忽略 abort 的迟到结果；关闭/重开详情重置状态并拒绝迟到回写。观察页没有聊天 POST，占用时连续查询不会释放占用，输入仍可用；查询不会以快照替代发送时原子获取。错误与延迟阶段由浏览器 fetch 注入，其余使用真实服务。

截图 execution-query-busy-1366.png（1366×900）与 execution-query-idle-1920.png（1920×900）位于 /private/tmp/agent-ui-preview/output/playwright，均已视查，无裁切或横向溢出。启动器报告临时服务关闭、隔离 schema/database 清理完成。未修改开发业务表、未调用真实模型，也未验证强制停机或遗留占用恢复。没有新增自动刷新、自动发送或释放功能。


### 2026-09-20：进程内执行并发预算组件

学习者完成 services/runtime/execution/execution_budget.py，核心无需修改；教练新增 tests/runtime/execution/test_execution_budget.py 共23条。使用真实 asyncio 事件循环与事件同步、AnyIO 取消域，不依赖数据库或模型。执行：

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/execution/test_execution_budget.py tests/runtime/execution/test_execution_threads.py
../../.venv/bin/python -m ruff check app tests
```

结果35 passed（0.07s），Ruff 与 git diff --check 通过。覆盖非法容量（含bool）、满额立即拒绝且不误归还、名额复用、只读属性/独立实例、20协程竞争3名额、业务异常保持原对象、重复Task取消/AnyIO取消、内层收尾等待期间仍占名额、已结束context重复退出不减掉其他名额、无运行循环拒绝、旧循环关闭后仍拒绝新循环、绑定后其他线程循环不得修改计数。

组件只承诺同一实例/事件循环作用域，不提供跨线程首次使用竞争安全，也不是跨进程总限额。内层收尾测试验证嵌套位置的含义，不代表实际聊天/线程收尾已接入预算。没有修改 HTTP、前端或数据库，本课不重复浏览器与数据库回归。下一课才装配到请求级生命周期。


### 2026-09-20：聊天入口共享进程内并发预算

学习者完成配置、请求依赖、API/BFF/UI安全错误映射；实际检查发现 main.py 尚未装配预算，教练补 lifespan 创建/清理实例。补 .env.example 的 AGENT_MAX_CONCURRENT_EXECUTIONS=2（未改本机 .env），测试应用显式装配预算；本地TestClient进入同一lifespan/事件循环，只替换迁移检查以使用隔离ORM schema，真实迁移启动检查仍由 migrations 专项验证。装配缺失继续安全500，不做生产fallback。

新增 test_execution_budget_config.py 17条、test_chat_execution_budget.py 6条；加强既有提交期间取消、ASGI两种断线/真实工具线程收尾的容量保持断言。20秒等待之类的时间不是准入依据，使用事件控制。最初加强测试保留了默认容量2却断言第二会话503，已将该受控场景明确设为容量1后通过；未修改生产容量语义。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -xq --tb=short
../../.venv/bin/python -m ruff check app tests
```

后端全量1371 passed（111.88s，-W error），Ruff/diff check通过。验证双入口不同会话共享容量、满额无Run/模型/新占用副作用、满额时本地不可访问会话仍404、缺失装配500、异常/会话忙/发送失败归还、重复取消后收尾保持容量、配置严格正整数及每次生命周期新建实例。真实PostgreSQL使用公共随机数据库/私有schema，自动清理。

前端 pnpm test:auth 207条（1.21s）、pnpm test:state 86条（1.70s）、pnpm typecheck、pnpm lint通过，新增BFF与聊天状态各1条503安全提示测试。启动配置默认2；仅限制单进程两个聊天入口，多进程不共享计数，独立工具演示入口不在此预算内。

```bash
AGENT_MAX_CONCURRENT_EXECUTIONS=1 BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=chat-budget.mjs \
PLAYWRIGHT_MODULE=/Users/wanxiancheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
.venv/bin/python apps/web/test/browser/run-isolated.py
```

真实API/BFF/隔离PostgreSQL、模拟模型的PC浏览器2组通过：两个不同会话竞争容量1，第二会话得到真实503并显示安全提示；取消第一会话后测试主动重试第二会话成功，刷新恢复持久历史。1366×900 budget-busy-1366.png 与1920×900 budget-recovered-1920.png 位于 /private/tmp/agent-ui-preview/output/playwright，已视查。测试重试只用于观察恢复，不是产品自动重试。启动器确认服务和隔离库/schema全部清理；未修改开发数据、未调用真实模型。遗留占用、强制停机与跨进程资源限额仍未解决。


### 2026-09-20：历史运行详情恢复摘要

学习者完成 historical-run-summary.ts 与 task-run-panel.tsx 的历史卡片接入，核心无需修正，仅补新文件末尾换行。新增 historical-run-summary.test.ts 23条：成功/指标型失败通过真实详情解析器恢复，null与零值区分，费用字符串精度保持，取消/未知状态/未结束/缺终态/重复冲突/状态不匹配拒绝；部分摘要或非法指标不拼接，事件信封type不被payload覆盖。Workspace全量703条通过（2.21s），聊天状态86条通过（1.93s），pnpm typecheck、pnpm lint、git diff --check通过。

```bash
cd apps/web
pnpm test:workspaces
pnpm test:state
pnpm typecheck
pnpm lint
```

浏览器命令（仓库根目录）：

```bash
BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=historical-run-summary.mjs \
PLAYWRIGHT_MODULE=/Users/wanxiancheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
.venv/bin/python apps/web/test/browser/run-isolated.py
```

4组通过：真实BFF/API/隔离PostgreSQL写入一次模拟模型运行，刷新并重新选择历史运行后摘要一致，观察阶段没有新聊天POST；受控历史失败响应显示2步、未知Token/模型耗时、真实0工具耗时及精确费用¥0.00128420；缺少摘要不生成卡片但保留事件；冲突终态不猜选摘要且保留时间线。后三组使用浏览器拦截详情响应，不宣称它们来自真实失败运行持久化。浏览器无pageerror，服务及随机库/schema已清理。

截图 /private/tmp/agent-ui-preview/output/playwright/historical-summary-1366.png 与 historical-summary-error-1920.png 已视查，右侧内容可滚动，数值无横向溢出。没有修改后端或开发业务表，未调用真实模型。本课不重复后端全量；上一课1371条证据保持其原范围。既有详情解析器会拒绝非法事件契约，因此损坏历史并非总能降级为无摘要的时间线。

### 2026-09-20：删除事务执行占用保护

按用户明确授权直接实现服务层：授权并取得会话行锁后查询占用存在性，存在则抛 ConversationBusyError 并回滚；不读取 token、不清理占用，仍只允许删除无消息/Run 的空任务。HTTP/BFF/UI占用错误映射尚未接入。

在 apps/api 执行：

```bash
../../.venv/bin/python -m pytest tests/tasks/test_task_deletion_service.py -q -W error -x --tb=short
../../.venv/bin/python -m pytest tests/tasks tests/runtime/execution/test_conversation_execution_service.py tests/local/test_task_conversation_boundary.py -q -W error -x --tb=short
../../.venv/bin/python -m ruff check app tests
```

删除专项38条通过（3.08s），其中新增11个场景；相关回归411条通过（30.90s，包含专项，不重复计数）。覆盖占用早于Run、终态后仍占用、外键插入与删除真实锁等待、删除提交/回滚、真实释放后可删、授权先行及查询失败回滚。Ruff与git diff --check通过。首次沙箱内测试无法连接本机PostgreSQL，授权网络访问后重跑通过；独立随机测试库/私有schema由公共夹具创建并自动清理，未修改开发业务表。未运行浏览器或调用模型，本次不重跑无改动的前端。

### 2026-09-20：删除执行占用冲突提示 API/BFF/UI

按用户授权完成：API将ConversationBusyError映射为409 conversation_busy及固定文案；BFF按状态/错误码白名单重建响应，UI作为明确拒绝保留任务、选中状态与草稿，允许手动重试，不自动删除或清理占用。所有响应继续no-store，未知错误仍为删除结果未确认。

在 apps/api 执行 `../../.venv/bin/python -m pytest tests/tasks/test_task_delete_api.py tests/tasks/test_task_deletion_service.py -q -W error -x --tb=short`：85条通过（10.68s），HTTP新增5条，覆盖无Run及running/done/error/aborted占用冲突、数据库记录保留、释放后空任务204及有历史仍409。公共夹具自动清理独立PostgreSQL库/schema。

在 apps/web 执行 `pnpm test:workspaces`、`pnpm test:state`、`pnpm typecheck`、`pnpm lint`：706/86条通过，类型检查及ESLint通过。BFF新增3条：允许409 conversation_busy，拒绝404/500搭配此码，剥离上游私有文案/字段/响应头。后端Ruff及git diff --check通过。

PC 浏览器定向2组通过（真实占用删除链路、既有明确拒绝/未知错误兜底）：

```bash
BROWSER_APP_MODE=local BROWSER_SCENARIO='delete UI execution,delete UI known' \
BROWSER_TEST_SCRIPT=task-delete.mjs \
PLAYWRIGHT_MODULE=/Users/wanxiancheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
.venv/bin/python apps/web/test/browser/run-isolated.py
```

一页保持模拟模型运行，另一页真实DELETE经BFF/API返回409并显示固定提示；任务、URL、输入保留，未自动重试。取消并轮询确认占用释放后，手动再删返回历史保护提示。未知错误仍阻止重复删除。1366×768及1920×1080截图delete-busy-1366.png/delete-busy-1920.png（/private/tmp/agent-ui-preview/output/playwright）已视查，无横向溢出。初次双标签页脚本等待停滞后中止并完成资源清理；补runner激活及显式超时后重跑通过。临时服务及随机测试库已清理，模型模拟，无真实付费调用。

### 2026-09-20：第4周历史删除与异常运行恢复收尾

有历史任务删除：授权并依次锁项目/任务/会话/Run，拒绝占用、未知状态或缺结束时间的运行；按事件→Run→消息→会话→Task清理，同事务提交。保留Workspace目录与TaskCreationRequest删除回执，原创建键不重建已删除任务。确认框明确删除历史、不会删除项目文件；未知响应仍保持结果未确认。

异常恢复：新占用和Run均保存仅服务端可见的机器标识摘要与PID。右侧“会话执行占用”先查询，再点“检查并恢复异常运行”；POST /sessions/{id}/execution/recover 经同源BFF，仅接受空对象，重新授权并持有会话/Run锁。只有当前机器能确认每个原进程已不存在时，才将running置aborted、保存EXECUTION_RECOVERED审计并清理占用，同事务提交；不覆盖既有终态，不重放消息、模型或工具。Run单独记录身份，覆盖创建提交成功但返回ID丢失、占用已经释放的情况。

恢复拒绝属于409，失败/超时是结果未确认，不自动重试。若原API仍活着，先停止原进程并重新启动服务，再明确发起恢复；不能仅凭停止按钮或时间长认定可以清理。PID复用为活进程也保守拒绝。历史无身份、非本机/PID namespace、读取权限或系统识别失败均拒绝；迁移不为旧记录捏造身份，旧版产生且无法验证来源的遗留记录不提供强制清理。此范围针对当前原生本机API及进程内工具线程；第5周引入Shell子进程前必须扩展进程树停止证明，不能将父进程退出等同于所有外部副作用已停止。Windows/Linux实现分支未在本轮实机验收，当前实机为macOS。

开发库顺序迁移1b8c75f140de→2c9d86a251ef→3da097b362fa，仅新增四个可空身份列，无业务记录删除；alembic check无结构差异。重新启动API使新代码生效。迁移/事务测试全部使用公共独立随机PostgreSQL库/schema并自动清理，未使用SQLite。

验证命令：

```bash
# apps/api
../../.venv/bin/python -m pytest -q -W error -x --tb=short
../../.venv/bin/python -m ruff check app tests
# apps/web
pnpm test:workspaces
pnpm test:state
pnpm typecheck
pnpm lint
# 仓库根目录：恢复后的显式继续执行、刷新及历史删除闭环
BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=week4-recovery.mjs \
PLAYWRIGHT_MODULE=/Users/wanxiancheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
.venv/bin/python apps/web/test/browser/run-isolated.py
```

新增测试覆盖真实子进程获取占用后被kill、存活进程拒绝、身份未知/外机/权限拒绝、slotless Run独立停止证据、旧占用不能替另一个Run证明停止、重复及迟到恢复、事务回滚、历史删除每个DELETE/commit失败全回滚、幂等删除回执保留与旧数据迁移。浏览器恢复夹具用已退出进程的真实PID准备持久化遗留状态，运行API/BFF/PostgreSQL真实，模型出口模拟；不宣称验证真实付费模型质量。

最终验证结果：后端全量1411 passed（105.58s，-W error），Workspace718条、聊天86条通过，TypeScript/ESLint/Ruff及git diff --check通过。历史迁移快照最初错误地包含新Run列，已将历史快照裁剪到对应修订，再经迁移/恢复82条及全量验证；不修改当前ORM来迁就旧测试。浏览器夹具先修正嵌套路由目录创建、完成文本精确定位；活跃冲突场景改为显式取消的`[cancel-held]`模拟，避免20秒固定等待在慢机器上提前结束。这些失败均已定位到验收配套，修正后重跑。

真实PC验收包括异常恢复→用户明确开始新一轮→刷新消息→删除历史任务；另有活跃执行时恢复409和删除409、取消并释放后历史删除成功、正常完成任务删除与未知错误兜底。模拟模型不产生真实API费用。1366×768及1920×1080截图保存在 /private/tmp/agent-ui-preview/output/playwright/week4-recovered-1366.png、week4-resumed-1920.png；原生系统进程退出证据在macOS实测。临时浏览器服务与测试数据库由启动器自动清理。

2026-09-20 Git收尾：按用户要求将第4周累计已验收源码、测试、两次执行身份迁移及对应课程/交接/题库文档统一归档至main并推送origin/main；以推送后的HEAD与远程main一致、工作区干净作为交接条件。本轮仅更新收尾说明，不重复执行已通过的功能测试。下一课在主仓库main的新任务中继续，核心实现恢复学习者亲手编写。


## 2026-09-20 第5周 Workspace 路径边界验收

学习者实现 `apps/api/app/services/workspace/workspace_path.py`，核心与参考一致，教练仅补末尾换行；新增 `apps/api/tests/workspace/test_workspace_path.py`。使用项目 Python 3.12 虚拟环境，在 `apps/api` 执行：

```bash
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/workspace/test_workspace_path.py
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/workspace tests/tasks tests/local
../../.venv/bin/python -m ruff check app tests
```

专项63条通过（1.54s）；受影响领域回归659条通过（46.18s）；全量后端Ruff及仓库 `git diff --check` 通过。首次沙箱内运行53条通过后因127.0.0.1:5432连接被拒绝而停在数据库夹具初始化；放行本机测试连接后完整通过，不是业务断言失败。新增测试的夹具导入先触发Ruff F811，改用项目既有模块引用方式后检查通过。

真实macOS临时文件系统覆盖普通文件/目录、Unicode与空格、内部文件及目录链接、外部链接、同名前缀兄弟目录、断链、循环链接、根目录被改为链接、缺失和非目录路径段。跨平台纯路径测试覆盖POSIX/Windows绝对路径、盘符相对路径、UNC/设备前缀、反斜杠、上级引用、设备名、替代数据流及控制字符；权限和通用系统异常采用故障注入，不宣称Windows实机验证。

数据库复用根conftest独立PostgreSQL库/私有schema，真实提交夹具数据后验证六类任务/项目/会话归属拒绝、未绑定目录拒绝、仅SELECT且无commit、文件系统处理前Session关闭、真实SQL错误传播与Session清理。测试资源随夹具自动清理，未操作开发业务表或迁移。

服务只返回检查时刻路径，允许目录，未读取内容、接入工具/HTTP/UI、调用模型或运行浏览器。不防止检查后并发替换，不证明同路径目录对象未改变，不提供Sandbox；下一课处理受限文本读取与打开阶段边界。


## 2026-09-20 受限文本文件读取验收

学习者完成 `apps/api/app/services/workspace/workspace_file.py`，核心与参考一致，教练仅补末尾换行。新增 `apps/api/tests/workspace/test_workspace_file.py` 35条，与上一课63条合计98条通过（1.69s）。按用户要求仅运行新增和直接相关测试，无领域或后端全量。在 `apps/api` 执行：

```bash
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/workspace/test_workspace_file.py tests/workspace/test_workspace_path.py
../../.venv/bin/python -m ruff check app/services/workspace/workspace_file.py tests/workspace/test_workspace_file.py
```

定向Ruff及 `git diff --check` 通过。真实macOS文件系统/描述符验证空文件、Unicode、恰好256KiB、超限、NUL/非法UTF-8、目录/FIFO/链接拒绝；确定性替换中间目录为链接、末端文件为链接/普通文件/FIFO时不读取替换对象。读取期间增长、缩小、同长度修改与增长超限会拒绝，实际读取量不超过上限+1；短读正确累积。跟踪真实os.open/os.close验证成功和异常后句柄清零，故障注入覆盖open/fstat/read异常脱敏及平台能力不足拒绝。

新增两条隔离PostgreSQL集成通过真实任务归属到文本读取（含项目内链接），确认文件读取前Session已关闭、仅SELECT，以及跨用户授权拒绝后不读取。数据库仍复用独立测试库/私有schema并自动清理，无开发表操作或迁移。未调用模型、未运行浏览器、未做Windows实机验收。该实现不是原子内容快照、硬链接来源/挂载隔离或完整Sandbox，普通文件I/O没有硬超时承诺。


## 2026-09-20 工具可信上下文构造验收

学习者完成tools/context.py及services/runtime/agent/tool_execution_context.py，核心与参考一致，仅补末尾换行；新增tests/runtime/agent/test_tool_execution_context.py。在apps/api执行：

```bash
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/agent/test_tool_execution_context.py
../../.venv/bin/python -m ruff check app/tools/context.py app/services/runtime/agent/tool_execution_context.py tests/runtime/agent/test_tool_execution_context.py
```

24条通过（1.20s，-W error），定向Ruff和git diff --check通过；仅运行本课单文件，不扩展Runtime、账号、路径或读取回归。测试复用根夹具的独立PostgreSQL库/schema并自动清理。验证local/account下相同归属条件、缺失/他人会话/他人项目/无Task拒绝、同用户不同会话和项目的正确定位、未绑定或不存在目录仍可构造上下文、只SELECT且无commit、真实SQL错误传播并关闭Session、不可变字段及拒绝独立Task/Workspace/root_path输入。未访问开发业务表/执行迁移，无浏览器或模型调用。

本课没有接入Runtime和工具注册，Context不可变不代表具备不可伪造的权限；安全来自可信身份来源与工厂查询，执行时仍须重新授权。首次Ruff命令误从仓库根使用API相对路径，未启动检查；改为apps/api工作目录后通过。


## 2026-09-20 工具执行器上下文透传验收

核心与参考一致，仅补registry.py类间空行。新增tests/runtime/agent/test_tool_context_dispatch.py 20条；按用户要求只选直接受影响的既有工具/线程/超时/取消/事件测试23条，共43条通过（1.17s，-W error）。在apps/api运行：

```bash
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/agent/test_tool_context_dispatch.py tests/tools/test_time_tool.py tests/runtime/agent/test_agent_execution_threads.py::test_success_and_error_preserve_protocol_and_tracker_ownership tests/runtime/agent/test_agent_execution_threads.py::test_abandoned_tool_is_still_tracked tests/runtime/agent/test_agent_execution_threads.py::test_rejected_tool_never_reaches_tracker tests/runtime/agent/test_agent_runtime.py::test_agent_loop_returns_timeout_as_observation tests/runtime/agent/test_agent_runtime.py::test_agent_loop_propagates_cancellation_during_tool_execution tests/runtime/agent/test_agent_runtime_events.py::test_stream_agent_loop_emits_successful_tool_sequence tests/runtime/agent/test_agent_runtime_events.py::test_stream_agent_loop_emits_failure_before_model_recovers
../../.venv/bin/python -m ruff check app/tools/registry.py app/services/runtime/agent/agent_runtime.py tests/runtime/agent/test_tool_context_dispatch.py
```

定向Ruff、git diff --check通过。新增验证缺失/错误类型上下文在启动线程前拒绝、直接execute防绕过、同一对象经两个入口和真实跟踪线程透传、并发两次执行不串上下文、无上下文工具签名兼容、保留字段及别名注册拒绝、额外参数不能覆盖context、错误参数模型拒绝、模型Schema不暴露上下文及注入参数校验拒绝。缺失上下文使用既有tool_execution_failed与固定details，不新增事件协议码。

无数据库/浏览器/模型调用，未跑整个Runtime或账号回归。文件工具仍未注册，聊天入口尚未装配上下文。


## 2026-09-20 只读文件工具适配与能力过滤验收

核心与参考一致，教练仅补errors.py/read_file.py末尾换行；更新test_time_tool.py旧断言，使默认TOOLS只包含无需上下文的注册工具。新增test_read_file_tool.py 37条；在apps/api运行：

```bash
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/tools/test_read_file_tool.py tests/tools/test_time_tool.py tests/tools/test_tools_api.py tests/model/test_model_decision.py tests/runtime/agent/test_tool_context_dispatch.py
../../.venv/bin/python -m ruff check app/tools/errors.py app/tools/read_file.py app/tools/registry.py app/services/model/model_decision.py app/services/runtime/agent/agent_runtime.py tests/tools/test_read_file_tool.py tests/tools/test_time_tool.py
```

合计83条通过（0.93s，-W error），定向Ruff与git diff --check通过。仅回归直接受影响的注册、演示API、模型适配和派发，不重复文件系统/数据库/浏览器或全量Runtime。模型及读取服务模拟，无真实模型调用。

覆盖参数严格类型/长度/额外身份字段拒绝，注册执行器仅使用context身份，成功JSON不含绝对路径或身份，12种业务失败安全转换，未知安全码拒绝，可见工具过滤与列表/嵌套Schema隔离，实际模拟模型请求无身份泄露，模型→Runtime→真实注册适配器→模拟读取→Observation→模型消息的成功/安全失败/未知失败/缺上下文链路。默认TOOLS不含文件工具，强行调用也被Runtime前置拒绝；聊天入口仍未装配上下文。


## 2026-09-20 流式聊天可信上下文装配验收

学习者chat_service.py核心与参考一致，无需改动。新增tests/chat/test_chat_tool_context.py 7条通过（0.42s）；定向流式回归9条通过（0.08s），生命周期4条通过（1.05s），共20条分组验收。在apps/api运行：

```bash
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/chat/test_chat_tool_context.py
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/chat/test_chat_stream.py -k 'redis_cancellation_signal or cancelled_stream or completed_stream or model_error or timed_out_stream or tool_error or non_completed_loop'
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/chat/test_chat_execution_lifecycle.py::test_cancel_during_database_commit_waits_then_finishes tests/chat/test_chat_execution_lifecycle.py::test_disconnect_during_real_tool_keeps_slot_until_thread_stops
../../.venv/bin/python -m ruff check app/services/chat/chat_service.py tests/chat/test_chat_tool_context.py tests/chat/test_chat_stream.py tests/chat/test_chat_execution_lifecycle.py
```

定向Ruff及git diff --check通过。新增用例验证local/account装配分支、查询在后台线程且先于历史/模型、同一上下文对象透传、授权或数据库类故障安全终态、取消查询后等待实际线程退出。两条真实隔离PostgreSQL+临时文件集成验证绑定读取成功/未绑定工具错误、模型实际可见工具、Observation、消息及Run/Event持久化；模型模拟且该专项关闭无关Token预算门槛。没有浏览器或真实模型验收，普通/chat未改为工具循环。

配套修正：旧流式Runtime替身不接受tool_context导致RUN_ERROR，已同步7个替身签名；旧生命周期夹具漏掉tool_execution_context.SessionLocal，新增查询曾落到默认连接而未进入预期保存阶段，已将其接入同一隔离库后重跑通过。该查询只读，未进行开发库写入或迁移；后续复用此夹具会正确隔离。首次Ruff命令从仓库根误用API相对路径未执行，切换apps/api后通过。未扩大到领域或后端全量回归。


## 2026-09-20 受限目录枚举服务验收

workspace_listing.py与参考一致，核心无需修改；新增tests/workspace/test_workspace_listing.py。只在apps/api运行本课专项：

```bash
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/workspace/test_workspace_listing.py
../../.venv/bin/python -m ruff check app/services/workspace/workspace_listing.py tests/workspace/test_workspace_listing.py
```

26条通过（1.11s，-W error），定向Ruff及git diff --check通过。真实macOS临时目录覆盖0/1/200/201/250项，跟踪迭代次数最多201次、截断和排序；Unicode/空格/隐藏项、文件/目录/内部外部断链/FIFO分类且不递归/不读取正文或链接目标；末端与祖先目录打开前替换为链接拒绝；子项stat前消失和目录元信息变化拒绝。open/scandir/stat权限与I/O错误故障注入验证脱敏，跟踪目录描述符和scandir迭代器在正常、截断与失败时关闭。能力不足明确拒绝。

3条隔离PostgreSQL验证成功授权后Session先关闭、仅SELECT、他人任务与未绑定目录不枚举；使用根夹具独立测试库/schema自动清理，未操作开发业务表或迁移。未跑读取/Runtime/聊天或全量回归，无真实模型和浏览器；Windows未实机验证。结果仅为单层有限子集，不提供稳定分页、全目录排序前缀、原子快照或完整Sandbox。目录工具尚未注册。


## 2026-09-20 目录工具适配与注册验收

核心与参考一致，仅补errors.py类间空行；更新test_read_file_tool.py有上下文工具集合，包含list_directory。新增test_list_directory_tool.py 31条，在apps/api运行：

```bash
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/tools/test_list_directory_tool.py tests/tools/test_read_file_tool.py tests/tools/test_time_tool.py
../../.venv/bin/python -m ruff check app/tools/list_directory.py app/tools/errors.py app/tools/registry.py tests/tools/test_list_directory_tool.py tests/tools/test_read_file_tool.py
```

77条通过（0.71s，-W error），定向Ruff及git diff --check通过。只测新增与直接相关适配/注册，不重复数据库、文件系统、聊天、Runtime全量或浏览器。模型与底层目录/读取服务模拟。

覆盖参数严格类型/长度/默认根目录、身份与recursive/limit拒绝、上下文准确透传、JSON保留条目类型和truncated、11类安全映射（含未来未知目录分类回退）、目录名来自实际Observation后再发起读取并三步完成、已知/未知故障转换为失败Observation、无上下文强行调用不触及服务。现有能力过滤无需业务改动，local流式入口可见目录和文件工具，普通/chat仍无工具循环。


## 2026-09-20 受限单文件文本搜索验收

workspace_search.py核心与参考一致，无需修改。新增tests/workspace/test_workspace_search.py，仅在apps/api执行本课专项：

```bash
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/workspace/test_workspace_search.py
../../.venv/bin/python -m ruff check app/services/workspace/workspace_search.py tests/workspace/test_workspace_search.py
```

43条通过（1.07s，-W error），定向Ruff及git diff --check通过。覆盖10种非法查询且不触发I/O、空格/128字符/Unicode保留、身份参数透传、不可变结果、字面量与大小写/首次命中、CRLF/CR/LF及Unicode分隔符规则、Python字符列号（含emoji和组合字符）、0/49/50/51/100匹配行、完整最长查询片段及坐标、10000字符正文上限与双截断、5类读取异常原样传播。

3条真实临时文件+隔离PostgreSQL集成覆盖成功、跨用户拒绝、非法UTF-8，确认搜索前数据库Session关闭、只SELECT。使用根夹具独立库/schema自动清理，无开发业务写入或迁移；未重复读取全套、聊天/Runtime/浏览器或模型验证。搜索服务尚未注册工具；字符输出上限不是精确JSON字节或Token上限，无原子文件快照保证。


## 2026-09-20 单文件搜索工具适配与注册验收

新增search_text_file适配器、invalid_search_query固定安全文案与requires_context注册。检查发现搜索注册覆盖原get_current_time条目，教练恢复原有时间注册；同步工具可见集合断言。新增test_search_file_tool.py 45条，在apps/api执行：

```bash
../../.venv/bin/python -W error -m pytest tests/tools/test_search_file_tool.py tests/tools/test_list_directory_tool.py tests/tools/test_read_file_tool.py tests/tools/test_time_tool.py -q
../../.venv/bin/ruff check app/tools/search_file.py app/tools/errors.py app/tools/registry.py tests/tools/test_search_file_tool.py tests/tools/test_read_file_tool.py
```

122条通过（0.91s，-W error），定向Ruff通过。仅新增和直接相关工具适配/注册测试，没有领域或后端全量回归。覆盖严格查询与路径参数、额外身份/能力字段拒绝、空格保留、上下文身份透传、空结果及两种截断JSON、11类安全错误；受控模型根据目录Observation选择文件后搜索并回答行号，已知/未知故障、无上下文强行调用及非法查询均安全失败。

模型与底层服务模拟，本轮未运行数据库、文件系统或浏览器验证，也未调用真实模型。三工具PC闭环仍待下一课；未提交推送。


## 2026-09-20 只读工具链 PC 闭环验收

新增apps/web/test/browser/readonly-tools.mjs与readonly_model.py；chat_test_app.py仅按专用标记分派受控模型。模型从真实目录Observation选择文件、根据真实搜索行号读取正文后生成答案；工具执行、权限、预算、BFF、持久化均使用实际实现，无业务代码修改。

仓库根目录运行：

```bash
BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=readonly-tools.mjs \
PLAYWRIGHT_MODULE=/Users/wanxiancheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
.venv/bin/python apps/web/test/browser/run-isolated.py
.venv/bin/python -m ruff check apps/web/test/browser/readonly_model.py apps/web/test/browser/chat_test_app.py
node --check apps/web/test/browser/readonly-tools.mjs
```

最终4组通过：浏览器键盘发消息后列目录→单文件搜索→读取→回答，三张工具卡片成功且行号真实；刷新恢复答案、3个工具结果事件持久化且没有重新POST聊天；上级路径读取拒绝且外部文件标记不泄露；未绑定目录以工具错误展示、模型解释后整轮正常结束。样例文件和外部文件内容未变，浏览器无pageerror。1366×900、1920×900页面无横向溢出，成功/失败截图已人工视查，位于/private/tmp/agent-ui-preview/output/playwright/readonly-tools-1366.png、readonly-tools-1920.png、readonly-escape.png和readonly-unbound.png。

首次因Chrome不能重读已消费的流式正文而失败，改为DOM展示加GET运行详情核对持久化事件；第二次发现右侧默认收起，补展开详情交互后通过。两次均为测试驱动修正，业务代码未变。Ruff、Node语法与git diff --check通过。只运行此浏览器专项，无账号或后端全量回归；独立数据库/schema走真实迁移并自动清理，临时服务及样例目录清理完成，无开发业务库写入。模型决策与用量模拟，不代表真实模型质量或费用验证。未提交推送。


## 2026-09-20 受限命令请求与结果契约验收

command_contracts.py核心与参考一致，仅清理末尾空白。新增tests/runtime/command/test_command_contracts.py，在apps/api执行：

```bash
../../.venv/bin/python -W error -m pytest tests/runtime/command/test_command_contracts.py -q
../../.venv/bin/python -m ruff check app/services/runtime/command/command_contracts.py tests/runtime/command/test_command_contracts.py
```

120条通过（0.10s，-W error），两文件Ruff与git diff --check通过。覆盖argv类型、数量、单项/总量上限、NUL及空程序拒绝、空参数和含空格/Shell字符参数保留；跨平台相对路径规则；额外身份/环境/超时/资源字段拒绝；公开Schema与JSON入口；零/非零/负退出码，超时/取消保留实际退出码，5类启动失败与矛盾字段拒绝；结果严格类型、两路65536字符上限、独立截断、JSON往返及不可变字段。

只运行上述单个纯校验专项，无数据库、文件系统操作、浏览器、模型或命令子进程验收，无领域/后端全量回归。契约接受合法语法的Shell请求不代表允许执行；固定超时和捕获字节常量未接执行器。Sandbox、实际输出限制及进程树收尾尚未实现。未提交推送。


## 2026-09-20 命令输出有界捕获缓冲验收

command_output.py核心与参考一致，Ruff发现参考实现的__slots__未排序，教练仅机械排序。新增tests/runtime/command/test_command_output.py，在apps/api运行：

```bash
../../.venv/bin/python -W error -m pytest tests/runtime/command/test_command_output.py -q
../../.venv/bin/python -m ruff check app/services/runtime/command/command_output.py tests/runtime/command/test_command_output.py
```

41条通过（0.05s，-W error），格式修正后两文件Ruff通过，git diff --check通过。覆盖两种额度严格类型及服务端硬上限、零额度/空输出、65535/65536/65537边界、大块及持续超限保存量保持、每个分块位置与逐字节UTF-8解码、非法及末尾不完整序列、控制字符保留、字节/字符独立及双重截断、非法输入不污染缓冲、finish幂等及拒绝后续空/非空块、不可变快照、两路独立与CommandResult JSON往返。

只运行新增单文件纯内存专项，未跑旧契约全套或领域全量，无数据库、真实管道、命令进程、模型或浏览器操作。本课只限制缓冲保存量；后续读取仍须固定块大小并在额度耗尽后排空管道。未提交推送。


## 2026-09-20 单路异步输出排空验收

command_stream.py核心与参考一致，无需修改。新增tests/runtime/command/test_command_stream.py，在apps/api运行：

```bash
../../.venv/bin/python -W error -m pytest tests/runtime/command/test_command_stream.py -q
../../.venv/bin/python -m ruff check app/services/runtime/command/command_stream.py tests/runtime/command/test_command_stream.py
```

31条通过（0.06s，-W error），两文件Ruff及git diff --check通过。真实内存StreamReader覆盖空流、4096/4097/70000字节、Unicode及延迟供给/EOF；受控读取器验证每次请求4096字节、短块不是EOF、零/已满额度继续排空、展示字符截断不提前结束、无效额度在读取前拒绝、错误返回类型/过大块拒绝、部分读取后异常原样传播。取消覆盖空流/部分输出后的阻塞读取，以及持续立即返回且保存额度为零时仍能调度取消；取消后流仍可使用，并发调用保持隔离。

仅本课新增专项，无旧缓冲/契约全套、领域或后端全量回归；没有真实管道、外部命令、数据库、模型或浏览器操作。单次块大小不等于整个进程内存上限，EOF或读取取消也不证明进程树停止。未提交推送。


## 2026-09-20 双路输出并发排空与失败收尾验收

command_capture.py核心与参考一致，无需修改。新增tests/runtime/command/test_command_capture.py，在apps/api执行：

```bash
../../.venv/bin/python -W error -m pytest tests/runtime/command/test_command_capture.py -q
../../.venv/bin/python -m ruff check app/services/runtime/command/command_capture.py tests/runtime/command/test_command_capture.py
```

12条通过（0.05s，-W error）。Ruff初次因目标版本未识别内置ExceptionGroup报告F821，测试显式从builtins导入后两文件检查通过；未调整仓库全局配置。git diff --check通过。

覆盖真实内存StreamReader空流/中文/任一路超限、独立缓冲及CommandResult往返/不可变成对快照；同一流拒绝且不消费；双方启动屏障证明并发；任一路先EOF仍等待另一侧；任一路OSError取消兄弟任务，收尾由Event阻塞时父任务不能提前返回；整体取消等待两路异步收尾；两路立即失败保留两个原始异常。终态检查没有遗留读取任务，测试有界超时防止错误实现挂住。

仅新增单文件专项，不重复旧单路/缓冲/契约全套，无领域全量、命令进程、真实管道、数据库或浏览器测试。TaskGroup读取任务结束不证明进程树停止；内部子任务CancelledError不按普通错误触发兄弟取消，不暴露任务句柄。未提交推送。


## 2026-09-20 命令环境显式白名单验收

command_environment.py核心与参考一致，无需修改。新增tests/runtime/command/test_command_environment.py，在apps/api运行：

```bash
../../.venv/bin/python -W error -m pytest tests/runtime/command/test_command_environment.py -q
../../.venv/bin/python -m ruff check app/services/runtime/command/command_environment.py tests/runtime/command/test_command_environment.py
```

62条通过（0.05s，-W error），两文件Ruff及git diff --check通过。覆盖精确11字段映射与目标路径派生；测试哨兵环境中的模型密钥、数据库/代理、解释器/动态加载注入变量不继承，替换os.environ为禁止读取对象仍可构造；两个路径字段的严格类型、绝对路径/非根/控制字符/上级引用/规范语法/长度边界；合法Unicode/空格原样保留、额外env/PATH/身份字段拒绝、独立返回字典及Path文件系统方法禁止调用。

仅本课单文件纯函数专项，没有启动命令、连接数据库、操作浏览器或运行领域/后端全量测试。只校验目标POSIX路径语法，不创建或验证隔离目录；LANG=C不承诺任意程序输出UTF-8。未接真实执行器，环境白名单不提供文件/网络隔离，固定PATH不限制可执行程序。未提交推送。


## 2026-09-20 Docker Sandbox 最小隔离验收

本机Darwin arm64，desktop-linux上下文，Docker客户端/服务端29.7.2，服务端linux/arm64。infra/sandbox/compose.yaml与参考一致。初检不存在指定Python镜像或练习容器；教练拉取官方镜像，随后固定到本次摘要：

```bash
SANDBOX_IMAGE=python@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e \
.venv/bin/python scripts/verify_sandbox.py
.venv/bin/python -m ruff check scripts/verify_sandbox.py
```

新增脚本强制摘要引用、使用随机sandbox-check项目名，finally停止并清理本轮容器，断言无遗留。3组真实专项通过：

1. 实际UID/GID10001、CapEff=0、NoNewPrivs=1、Seccomp=2；根挂载只读，/etc写入拒绝；HOME和/tmp可写且各16MiB，nosuid/nodev/noexec生效；无宿主bind或Docker Socket，容器不可见宿主临时哨兵。实际cgroup为128MiB内存、0额外swap、32进程、0.5CPU；这是配置生效检查，没有运行OOM或进程耗尽压力测试。读取等待主进程/proc环境验证精确11字段，而非仅检查镜像环境。
2. 网络none下非回环接口均未启用，连接1.1.1.1:443以不可达拒绝；额外存在gre等内核隧道接口，初始“接口名称只有lo”的测试断言失败，改为网络命名空间接口标志及实际外连检查后通过。没有改变产品网络限制。
3. 生成忽略SIGTERM并独立会话的两代进程，Compose停止后daemon报告Running=false/Pid=0，top/exec拒绝；重启后tmpfs哨兵消失，宿主哨兵不变。此处信任Docker daemon停止语义，不宣称已证明抵御容器逃逸。

脚本输出分为隔离配置、子孙进程停止、tmpfs重启三组PASS。Ruff及git diff --check通过，临时容器与宿主哨兵目录已清理，Python镜像保留本地便于后续课程。没有挂载真实项目，没有改动数据库/Redis配置或业务表；只跑本课真实专项，无全量回归。尚未接入通用命令执行器、模型工具、项目挂载与异常恢复。未提交推送。


## 2026-09-20 Docker Sandbox 创建参数构造验收

sandbox_spec.py核心与参考一致。Ruff要求集合内隐式字符串拼接有括号，教练仅为两处tmpfs参数补括号，不改变内容。新增tests/runtime/sandbox/test_sandbox_spec.py，在apps/api运行：

```bash
../../.venv/bin/python -W error -m pytest tests/runtime/sandbox/test_sandbox_spec.py -q
../../.venv/bin/python -m ruff check app/services/runtime/sandbox/sandbox_spec.py tests/runtime/sandbox/test_sandbox_spec.py
```

44条通过（0.05s，-W error），两文件Ruff及git diff --check通过。首次测试收集因教练使用pytest保留参数名request失败，改名invalid_request后通过。

覆盖固定Docker选项精确集合/数量、镜像前后分层、env -i/--及11字段映射、参数空格/换行/引号/选项/赋值/Shell字符原样保留；程序首项相对路径/选项/等号拒绝；执行token类型/格式/末尾换行拒绝；非批准镜像摘要拒绝；非默认工作目录和错误请求类型拒绝；请求列表清空/NUL/超长/上级路径等构造后修改被重新校验；快照不可变且与原列表解耦，不同token名称隔离。

仅本课新增纯参数单文件专项，没有调用Docker或执行命令，无数据库/浏览器或全量回归。此处只接受working_directory为点号并使用临时/tmp，尚未支持项目挂载；停止宽限不等于运行超时，名称标签不代替后续容器ID和归属核对。未提交推送。


## 2026-09-20 Sandbox 创建响应解析与身份确认验收

sandbox_identity.py核心与参考一致，无需修改。新增tests/runtime/sandbox/test_sandbox_identity.py，在apps/api执行：

```bash
../../.venv/bin/python -W error -m pytest tests/runtime/sandbox/test_sandbox_identity.py -q
../../.venv/bin/python -m ruff check app/services/runtime/sandbox/sandbox_identity.py tests/runtime/sandbox/test_sandbox_identity.py
```

95条通过（0.07s，-W error），两文件Ruff及git diff --check通过。覆盖64位小写ID、可选单个LF/CRLF与日志/多行拒绝；确认阶段拒绝未规范化ID；异常类型/JSON/深嵌套及65536字符边界；单元素数组与嵌套对象类型；ID/名称/镜像/标签缺失或不匹配；created/Running严格布尔/Pid严格整数零；重复字段（含嵌套）、NaN/Infinity拒绝；额外字段兼容、跨执行spec拒绝、不可变身份快照及固定安全错误。

仅本课单文件纯解析专项，无Docker或外部命令、数据库、浏览器及全量回归。尚未接实际创建/inspect调用；文本长度校验不代替传输层字节限制，身份确认不完整复核HostConfig或授权启动。失败代表无法确认，不证明容器不存在。未提交推送。


## 2026-09-20 有界 Docker 客户端验收

从 apps/api 执行：

```bash
../../.venv/bin/python -m pytest tests/runtime/docker/test_docker_client.py tests/runtime/command/test_command_capture.py -q -W error
../../.venv/bin/python -m ruff check app/services/runtime/docker/docker_client.py tests/runtime/docker/test_docker_client.py
```

新增33条，连同直接相关双路捕获12条共45条通过（0.87s）；定向Ruff及git diff --check通过。核心与参考一致，仅补两处说明理由的BLE001豁免，统一异常脱敏与启动失败接收行为不变。

覆盖固定程序/socket/argv/最小环境、非法token不启动、非零退出/截断/非法UTF-8拒绝、启动异常脱敏、启动和运行阶段超时/取消、重复取消等待reap、捕获失败排空、kill竞态及收尾异常不冒充成功。5条真实Python子进程测试覆盖双路65536字节边界、双路200000字节超限排空、超时、提前关闭管道但仍运行、取消后SIGKILL退出；其余为事件屏障控制的进程替身，不宣称真实Docker取消场景已验证。

初次受限环境访问Docker失败；授权访问本机socket后，使用本课调用底座只读查询Server.Version得到29.7.2，并用随机token验证不存在容器inspect返回docker_client_failed。不创建、启动或删除容器，无数据库/浏览器及全量回归。当前只适配macOS Docker Desktop；10秒不包含完成清理所需的额外等待，客户端退出不证明daemon回滚操作。未提交推送。


## 2026-09-20 Sandbox 创建与身份确认装配验收

从 apps/api 执行：

```bash
../../.venv/bin/python -m pytest tests/runtime/sandbox/test_sandbox_creation.py tests/runtime/docker/test_docker_client.py -q -W error
RUN_SANDBOX_CREATION_DOCKER=1 ../../.venv/bin/python -m pytest tests/runtime/sandbox/test_sandbox_creation.py -k real_docker -q -W error
../../.venv/bin/python -m ruff check app/services/runtime/docker/docker_client.py app/services/runtime/sandbox/sandbox_creation.py tests/runtime/sandbox/test_sandbox_creation.py
```

常规66条通过、2条Docker默认跳过（0.89s）；显式授权访问本机Docker socket后，2条真实Docker专项通过（0.27s）。新增合计35条，既有直接相关客户端33条；无全量回归。核心与参考一致，仅补客户端函数间空行/末尾换行，定向Ruff及diff check通过。

常规覆盖真实规格构造/严格解析组合、执行顺序及不可变快照、非法token/请求/工作目录/变异请求在调用前拒绝；create/inspect超时、输出不可用和未知异常统一未确认且保留token/名称；创建输出损坏不继续inspect；ID/名称/标签/镜像/状态不匹配拒绝；两阶段取消传播并等待依赖收尾；create适配只传固定规格参数，拒绝错误类型/前缀。

真实Docker使用两组随机token，固定批准镜像、无挂载、只创建不启动。成功路径返回created身份；第二组先真实创建，再在测试适配器中人工抛出超时模拟响应丢失，验证服务未重试、容器仍存在。finally重新核对完整ID/名称/标签/镜像及created状态后按ID执行非强制rm，再查询确认该ID无残留。未修改其他容器、未启动命令。该故障是受控注入，不是真实daemon网络故障；持久记录、恢复核对与启动授权尚未实现。无数据库/浏览器/模型调用，未提交推送。


## 2026-09-20 创建结果只读核对验收

从 apps/api 执行：

```bash
../../.venv/bin/python -m pytest tests/runtime/sandbox/test_sandbox_reconciliation.py tests/runtime/sandbox/test_sandbox_identity.py -q -W error
RUN_SANDBOX_RECONCILIATION_DOCKER=1 ../../.venv/bin/python -m pytest tests/runtime/sandbox/test_sandbox_reconciliation.py -k real_docker -q -W error
../../.venv/bin/python -m ruff check app/services/runtime/sandbox/sandbox_identity.py app/services/runtime/sandbox/sandbox_reconciliation.py tests/runtime/sandbox/test_sandbox_reconciliation.py
```

新增50条常规专项，与直接相关身份解析95条共145条通过、1条Docker默认跳过（0.13s）；显式授权访问本机Docker socket后1条真实Docker通过（0.27s）。定向Ruff及diff check通过。核心与参考一致，仅补身份模块函数间空行/末尾换行。

覆盖单次固定inspect调用且无其他Docker操作、未知/已知ID成功、已知ID不匹配不降级发现、非法原上下文/ID调用前拒绝、查询失败/未知异常安全未确认、两路径解析失败、严格JSON/重复字段/非标准数值/长度边界、候选ID及名称标签镜像状态核对、不可变快照、取消传播与依赖收尾。

真实Docker仅创建一个随机token临时容器，不启动：未知/已知ID核对返回相同身份；错ID拒绝后重新核对仍为原created目标；finally严格复核后按完整ID非强制删除并查询确认无残留；删除后核对仍返回SandboxCreationUnconfirmed，不把通用客户端错误解释成可靠不存在。无数据库、浏览器、模型或全量回归；未验证真实daemon失联或进程崩溃恢复。未提交推送。


## 2026-09-20 已确认未启动容器显式清理验收

用户本课明确授权教练直接实现。新增sandbox_cleanup.py，扩展docker_client.py固定非强制删除及全部状态的完整ID缺失查询；未注册模型工具或接自动补偿。

从 apps/api 执行：

```bash
../../.venv/bin/python -m pytest tests/runtime/sandbox/test_sandbox_cleanup.py tests/runtime/docker/test_docker_client.py -q -W error
RUN_SANDBOX_CLEANUP_DOCKER=1 ../../.venv/bin/python -m pytest tests/runtime/sandbox/test_sandbox_cleanup.py -k real_cleanup -q -W error
../../.venv/bin/python -m ruff check app/services/runtime/docker/docker_client.py app/services/runtime/sandbox/sandbox_cleanup.py tests/runtime/sandbox/test_sandbox_cleanup.py
```

新增41条常规专项，直接相关客户端33条，共74条通过、2条真实Docker默认跳过（0.90s）。显式授权Docker socket后，2条真实专项通过（0.48s）。定向Ruff及diff check通过；测试字典写法按Ruff机械调整，无业务问题。

覆盖固定inspect→rm完整ID→ls全部状态/no-trunc/完整ID过滤顺序、不可变完成快照、非法上下文/ID调用前拒绝、ID/名称/标签/镜像/运行及exited状态不符不删、各阶段异常安全未确认/无重试、删除后仍存在拒绝、删除回执与查询输出严格校验、三阶段取消传播与依赖收尾。底层缺失查询只有成功空输出才返回True，失败不当作缺失；不支持按名称或短ID删除，无force/stop。

真实Docker两组随机token，仅创建不启动：错ID请求未删除；正常路径完成，另一组真实删除后在测试适配器人工注入超时，服务仍报告清理结果未确认且只删一次。两组均确认完整ID无残留；重复显式请求因inspect失败仍未确认且未再次rm。finally只在目标仍存在且严格身份复核通过后按ID兜底清理。响应丢失为人工注入，不是真实网络故障；未验证宿主高权限并发状态变更。无数据库/浏览器/模型或全量回归，未提交推送。


## 2026-09-20 启动前执行配置复核验收

从 apps/api 执行：

```bash
../../.venv/bin/python -m pytest tests/runtime/sandbox/test_sandbox_execution_policy.py -q -W error
RUN_SANDBOX_EXECUTION_POLICY_DOCKER=1 ../../.venv/bin/python -m pytest tests/runtime/sandbox/test_sandbox_execution_policy.py -k real_unstarted -q -W error
../../.venv/bin/python -m ruff check app/services/runtime/sandbox/sandbox_execution_policy.py tests/runtime/sandbox/test_sandbox_execution_policy.py
```

71条纯校验通过、1条Docker默认跳过（0.08s）；显式授权访问Docker socket后1条真实专项通过（0.22s）。核心与参考一致；两处隐式字符串拼接按Ruff加括号，定向Ruff/diff check通过。未改策略值。

覆盖必需字段缺失、用户/工作目录/入口类型及内容、Cmd顺序/分隔符/env赋值/程序/参数改变、环境类型/缺失/额外/重复/改值及顺序兼容、交互字段严格False、健康检查拒绝、严格JSON/身份错误转换、原请求重校验、不可变身份和无输入修改。Cmd测试独立列出11字段及原命令，避免仅镜像规格构造器实现。

真实容器由现有创建服务生成，不启动；实际inspect执行配置通过，与批准镜像Config.Env基线一致（PYTHON_VERSION=3.12.14）。只在响应副本添加LD_PRELOAD验证拒绝，未向真实容器注入；finally经已验收清理服务删除并成功查询确认无残留。本课没有HostConfig/挂载/资源检查，不是完整启动授权。仅新增测试，无数据库/浏览器/模型或全量回归。此前candidate_id的isinstance收窄修复已有145条相关测试通过，本课未重复；未提交推送。


## 2026-09-20 Sandbox 隔离与资源配置复核验收

从 apps/api 执行：

```bash
../../.venv/bin/python -m pytest tests/runtime/sandbox/test_sandbox_isolation_policy.py -q -W error
RUN_SANDBOX_ISOLATION_POLICY_DOCKER=1 ../../.venv/bin/python -m pytest tests/runtime/sandbox/test_sandbox_isolation_policy.py -k real_created -q -W error
../../.venv/bin/python -m ruff check app/services/runtime/sandbox/sandbox_isolation_policy.py tests/runtime/sandbox/test_sandbox_isolation_policy.py
```

最终218条纯校验通过、1条Docker默认跳过（0.16s）；真实Docker专项1条通过（0.22s）。初版参数化包含5个值相同而提前返回的组合，已移除并重跑纯校验，最终计数不含这些空断言组合；真实测试内容未变化。核心与参考一致，无需修改；定向Ruff/diff check通过。

覆盖字段缺失、布尔/数值严格类型、权限/网络/命名空间变化、资源浮点/字符串/无限额及偏差、可选列表null/空与异常类型、端口映射/镜像卷、tmpfs缺失/额外/选项变化、顶层挂载类型/目标/重复/读写标记、重启/日志嵌套结构；复用真实执行配置/身份解析，确保前置失败转换为固定安全错误，输入不被修改。fixture独立列出策略值。

真实Docker仅创建一个随机token容器，不启动；实际inspect通过全部本课策略。仅在响应副本中将Privileged改为True并验证拒绝，不创建真实特权或宿主挂载容器。finally经已验收清理服务删除并查询确认无残留。此处只验证声明配置，不证明运行时cgroup/挂载生效，不穷举所有Docker安全字段。仅新增专项，无数据库/浏览器/模型及全量回归；未提交推送。


## 2026-09-20 容器停止与停止状态确认验收

本课用户授权教练实现。新增sandbox_stop.py，抽出sandbox_identity.read_sandbox_identity共享严格归属解析，原confirm_created_sandbox_identity仍仅接受created；扩展Docker完整ID inspect和stop适配，固定SIGTERM与2秒宽限。

从 apps/api 执行：

```bash
../../.venv/bin/python -m pytest tests/runtime/sandbox/test_sandbox_stop.py tests/runtime/sandbox/test_sandbox_identity.py tests/runtime/docker/test_docker_client.py -q -W error
RUN_SANDBOX_STOP_DOCKER=1 ../../.venv/bin/python -m pytest tests/runtime/sandbox/test_sandbox_stop.py -k real_stop -q -W error
../../.venv/bin/python -m ruff check app/services/runtime/sandbox/sandbox_stop.py app/services/runtime/sandbox/sandbox_identity.py app/services/runtime/docker/docker_client.py tests/runtime/sandbox/test_sandbox_stop.py
```

新增53条常规，与身份95条/客户端33条共181条通过，1条Docker默认跳过（0.97s）；授权Docker专项1条通过（2.98s）。定向Ruff/diff check通过。

覆盖created/running/exited一致状态、字段缺失/错误类型、Paused/Restarting/Dead及中间态拒绝、已停不发送stop、身份不符不停止、固定完整ID及信号/宽限、stop回执拒绝、停止后仍running未确认、三阶段超时/取消不自动后续操作、不重试与错误脱敏。共享归属解析重构通过原身份全部专项。

真实测试在随机受限容器创建后先通过隔离策略复核，再仅在测试准备阶段直接start；未新增产品启动入口。父进程和独立会话子进程均忽略SIGTERM，测试读取两个就绪文件确认处理器安装后调用停止服务。停止后daemon报告exited/Running=false/Pid=0且exec拒绝，再次停止只读返回。finally核对原完整身份及已停止状态后非强制rm，并成功查询无残留。证据依赖可信daemon，不宣称证明防容器逃逸；不改变created清理服务。无数据库/浏览器/模型及全量回归，未提交推送。

停止参数语义参考：https://docs.docker.com/reference/cli/docker/container/stop/ 。停止请求超时或取消不代表容器已停止，调用方仍需保留token/ID；自动取消收尾、异常中间态恢复和启动编排尚未接入。


## 2026-09-20 容器启动与启动结果确认验收

本课用户授权教练实现sandbox_start.py和Docker完整ID start适配。开始时复制并重新验证请求，await期间外部修改不影响检查或停止收尾；启动前复用隔离/执行与严格状态解析，启动后重新查询running/exited。开始尝试start后的失败调用stop_and_confirm_sandbox；独立shield任务等待收尾，重复取消不打断，取消最终以CancelledError子类传播。异常/取消携带token/ID及stop_confirmed。未尝试start时不猜测性停止，不自动删除或重试start。

从 apps/api 执行：

```bash
../../.venv/bin/python -m pytest tests/runtime/sandbox/test_sandbox_start.py tests/runtime/docker/test_docker_client.py -q -W error
RUN_SANDBOX_START_DOCKER=1 ../../.venv/bin/python -m pytest tests/runtime/sandbox/test_sandbox_start.py -k real_start -q -W error
../../.venv/bin/python -m ruff check app/services/runtime/sandbox/sandbox_start.py app/services/runtime/docker/docker_client.py tests/runtime/sandbox/test_sandbox_start.py
```

新增30条常规，客户端33条，共63条通过、4条Docker默认跳过（0.91s）；真实Docker4条通过（1.23s）。定向Ruff/diff check通过。统一异常处理增加有理由BLE001注释，取消不被吞掉。

覆盖running/快速exited、非法ID/上下文、策略失败不start/stop、前后归属变化、三阶段调用失败、错误状态、停止失败未确认、三阶段取消及重复取消等待收尾、调用方请求变异不影响收尾、普通失败收尾中取消改为取消语义、start回执严格校验。无遗留收尾任务。

真实测试4组随机受限容器：持续running、通过测试查询屏障覆盖快速exited、真实start返回后人工注入响应丢失、真实start后取消。后两组stop_confirmed=True且start仅一次；最后逐一停止核对后按完整ID非强制删除并成功查询无残留。失联为受控注入，不是真实daemon网络故障。未测试完整命令执行输出、全局超时或启动成功后的取消；日志driver=none，后续需要启动前attach协调。无数据库/浏览器/模型及全量回归，未提交推送。


## 2026-09-20 命令退出结果与退出码确认验收

本课用户授权教练实现sandbox_exit.py。先复用严格身份/状态解析，只接受一致exited停止证据；再从同一响应解析严格整数0～255 ExitCode、严格布尔OOMKilled与字符串Error。结果不可变，Error只公开是否非空；succeeded为退出码0且无OOM/daemon错误。不从137推断OOM，不从143推断取消，不返回未采集的输出或耗时。

从 apps/api 执行：

```bash
../../.venv/bin/python -m pytest tests/runtime/sandbox/test_sandbox_exit.py -q -W error
RUN_SANDBOX_EXIT_DOCKER=1 ../../.venv/bin/python -m pytest tests/runtime/sandbox/test_sandbox_exit.py -k real_exit -q -W error
../../.venv/bin/python -m ruff check app/services/runtime/sandbox/sandbox_exit.py tests/runtime/sandbox/test_sandbox_exit.py
```

75条纯解析通过、3条Docker默认跳过（0.09s）；3条真实Docker专项通过。定向Ruff/diff check通过。覆盖退出码与OOM/daemon错误组合、不可变/无输入修改及错误脱敏、类型/范围/字段缺失、非exited和停止矛盾、严格JSON/身份失败、非法原请求仍属前置错误。

真实Docker三个随机受限容器经创建与启动服务运行SystemExit(0/7/137)，测试限定5秒轮询等待exited后调用纯解析；结果与指定退出码一致、OOMKilled=false、daemon_error=false。137由程序主动退出产生，实证不能只靠137认定OOM。未做真实内存耗尽压力测试；OOM分支仅纯解析覆盖。finally逐一核对停止后按ID非强制删除，成功查询确认无残留。无数据库/浏览器/模型或全量回归，未提交推送。尚无命令输出/全局超时编排，下一步需attach协议与启动协调。


## 2026-09-20 Docker attach 输出帧解析验收

从 apps/api 执行：

```bash
../../.venv/bin/python -m pytest tests/runtime/docker/test_docker_attach_parser.py -q -W error
../../.venv/bin/python -m ruff check app/services/runtime/docker/docker_attach_parser.py tests/runtime/docker/test_docker_attach_parser.py
```

91条纯字节专项通过（0.09s），核心与参考一致，无需修改。定向Ruff/diff check通过。

覆盖所有单切分位置、跨帧UTF-8的所有双切分位置、8种固定块大小、8组固定种子随机帧与分块、空帧与通道交错、65536/65537/1MiB正文及独立捕获预算、超限后另一通道后续帧继续解析、头缓冲及捕获保存量受限、无效通道/保留字节/超长声明、半头/半正文EOF、协议失败后feed/finish持续拒绝、错误类型/超大输入不推进位置、正文伪头不重解释、空流/完成幂等/完成后拒绝写入、实例隔离与截断UTF-8。

本课固定单输入4096字节、单帧正文1MiB为应用策略，Docker长度字段本身是uint32；仅接收通道1/2，stdin关闭场景拒绝通道0。原始字节两路各捕获65536字节，无整帧正文缓冲。解析完成不代表网络正常EOF或命令退出，读取/HTTP握手/关闭连接/停止容器仍待接。无Docker、网络、外部进程、数据库、浏览器或全量回归；未提交推送。


2026-09-20收尾：累计学习源码、配套测试和既有文档统一提交至main；此前各课“未提交推送”为当时状态。本次仅做累计变更静态检查与git差异检查，不重复各课已经通过的运行验收或全量回归。远端同步结果以Git记录为准。


## Runtime 职责分组验证（2026-09-20）

服务29个模块分到 agent/execution/command/sandbox/docker；31个测试模块对应移动。所有29个服务迁移前后的AST在还原导入路径后完全一致；没有业务逻辑修改或旧路径转发壳。调用方、字符串形式的monkeypatch目标、跨测试夹具引用、浏览器启动器和本文历史命令已同步新路径。

从 `apps/api` 执行定向验证：

```bash
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime tests/chat/test_chat_execution_lifecycle.py tests/chat/test_chat_execution_budget.py tests/chat/test_chat_tool_context.py tests/chat/test_conversation_execution_api.py tests/chat/test_execution_recovery_api.py tests/local/test_task_conversation_boundary.py tests/tasks/test_task_deletion_service.py tests/tools tests/model
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/local/test_task_conversation_boundary.py tests/tasks/test_task_deletion_service.py tests/tools tests/model
../../.venv/bin/python -W error -m pytest --collect-only -q
../../.venv/bin/python -m ruff check app tests ../web/test/browser/chat_test_app.py ../web/test/browser/readonly_model.py ../web/test/browser/run-isolated.py
```

首次沙箱执行因本机TCP连接被禁止而产生数据库夹具错误；放行后第一批1457条通过、15条跳过，在本地流式会话用例发现既有夹具遗漏了工具上下文的SessionLocal替换。补齐隔离连接后，该组及尚未执行部分233条通过（8.21s）。两批去重合计1669条通过、15条跳过；未再次重跑已通过部分。测试使用随机独立PostgreSQL库和私有schema，自动清理。未开启显式Docker集成测试开关。

全部2857条后端测试收集通过（只收集，不执行全量回归），后端及受影响浏览器Python夹具Ruff通过，旧服务路径扫描与git diff --check通过。本轮未运行真实浏览器、真实模型或Docker生命周期集成，不涉及数据库迁移。下一课仍是attach异步读取与收尾。


## attach 异步读取与收尾（2026-09-21）

学习者新增 `apps/api/app/services/runtime/docker/docker_attach_stream.py`，核心与参考一致，无需修正。教练新增 `apps/api/tests/runtime/docker/test_docker_attach_stream.py`。

从 `apps/api` 执行：

```bash
../../.venv/bin/python -W error -m pytest -q tests/runtime/docker/test_docker_attach_stream.py tests/runtime/docker/test_docker_attach_parser.py
../../.venv/bin/python -m ruff check app/services/runtime/docker/docker_attach_stream.py tests/runtime/docker/test_docker_attach_stream.py
```

新增41条，连同解析器91条共132条通过（0.13s），两文件Ruff及git diff --check通过。真实内存StreamReader与受控读取器覆盖短块/跨帧UTF-8/通道交错、空流、4096/65536/65537/1MiB正文、超限后继续读取、半帧EOF、无效返回类型与超大块、读取异常原样传播、协议错误不再读取、延迟正文及EOF、等待读取取消、额度耗尽后立即读取仍可取消、取消不关闭流及并发隔离。

只测试本课及直接依赖的帧解析器；无外部进程、Docker、网络、数据库、浏览器或全量回归。正常返回只代表读到EOF并位于完整帧边界，不证明订阅无遗漏或命令退出成功。HTTP握手、连接关闭及启动前订阅协调尚未接通，未提交推送。


## attach HTTP 响应读取与校验（2026-09-21）

学习者新增 `apps/api/app/services/runtime/docker/docker_attach_http.py`，核心与参考一致，无需修改；教练新增对应 `tests/runtime/docker/test_docker_attach_http.py`。

从 `apps/api` 执行：

```bash
../../.venv/bin/python -W error -m pytest -q tests/runtime/docker/test_docker_attach_http.py tests/runtime/docker/test_docker_attach_stream.py
../../.venv/bin/python -m ruff check app/services/runtime/docker/docker_attach_http.py tests/runtime/docker/test_docker_attach_http.py
```

新增70条，与直接相关读取41条共111条通过（0.71s），两文件Ruff及git diff --check通过。覆盖同块响应头/首帧完整保留、大小写/多token、状态码及协议版本、缺失/重复/非法字段、内容类型与编码拒绝、8191/8192/8193字节边界、提前EOF、读取契约与原始异常、等待超时、滴流不重置总预算、立即读取超时、外部取消及并发隔离。

8KiB与10秒是项目策略；仅接受101升级和multiplexed-stream，不兼容200/raw-stream。逐字节消费仅限制本层头部缓冲，不宣称限制底层传输缓冲。失败/取消不关闭借用的reader；请求发送、固定socket连接、连接拥有者关闭及启动前订阅尚未接入。全部为内存/受控读取测试，无真实Docker、网络、数据库、浏览器或全量回归，未提交推送。


## attach 请求构造（2026-09-21）

学习者在 `apps/api/app/services/runtime/docker/docker_attach_http.py` 新增固定API版本与build_docker_attach_request，核心与参考一致；教练仅补两处顶层函数间空行，并在既有test_docker_attach_http.py追加34条专项。

从 `apps/api` 执行：

```bash
../../.venv/bin/python -W error -m pytest -q tests/runtime/docker/test_docker_attach_http.py
../../.venv/bin/python -m ruff check app/services/runtime/docker/docker_attach_http.py tests/runtime/docker/test_docker_attach_http.py
```

同文件共104条通过（0.64s，-W error），两文件Ruff及git diff --check通过。覆盖精确ASCII请求及CRLF/无正文、4组有效完整ID、23组非法类型/长度/大小写/空白/路径/查询/换行/NUL/Unicode输入、策略覆盖参数拒绝、keyword-only、重复调用与目标隔离、环境变量不能覆盖Host/API版本以及禁止文件/socket调用。

固定API 1.45与Host: docker；Host字段不决定实际socket目标，ID格式正确不表示授权。没有启动连接、容器或其他外部进程；未验证本机daemon的API兼容，无数据库/浏览器/全量回归。下一课接固定socket的连接所有权与发送/握手/关闭，未提交推送。


## 固定 socket 的 attach 连接生命周期（2026-09-21）

用户明确授权教练完成本课核心。新增docker_attach_connection.py与对应测试；固定本机Docker Desktop socket，不受DOCKER_HOST覆写；请求构造在资源分配前验证ID。原始socket在连接/流装配前由本层持有，取得writer后转交传输；10秒预算覆盖connect、发送drain和HTTP握手。只在成功后借出reader，预算不覆盖命令寿命。退出由唯一shield关闭任务持有writer，2秒优雅关闭失败请求abort并报告未确认；重复取消先完成关闭任务再传播，不遗留后台任务。业务异常不被连接错误包装，关闭失败附固定说明而不覆盖原异常；关闭连接不证明容器停止。

从 `apps/api` 执行：

```bash
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/docker/test_docker_attach_connection.py tests/runtime/docker/test_docker_attach_http.py tests/runtime/docker/test_docker_attach_stream.py
RUN_ATTACH_SOCKET_TESTS=1 RUN_ATTACH_DOCKER_TESTS=1 ../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/docker/test_docker_attach_connection.py -k real
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/docker/test_docker_attach_connection.py -k 'share_one_budget or retained_when_close'
../../.venv/bin/python -m ruff check app/services/runtime/docker/docker_attach_connection.py tests/runtime/docker/test_docker_attach_connection.py
```

常规第一批170条通过、2条显式集成跳过（0.86s），追加总预算及握手/关闭同时失败2条通过（0.10s）；去重172条通过，其中本课新增27条。覆盖成功请求/首帧保留、连接/装配/发送/握手失败、各等待阶段超时或取消、业务异常原样传播、关闭异常/超时abort及证据、重复取消等待收尾、握手预算不覆盖正文、非法ID分配前拒绝和无关闭任务遗留。两文件Ruff及git diff --check通过。

获得本机socket执行权限后，2条真实验证通过（0.21s）：临时Unix server确认精确请求、同块首帧和关闭后的对端EOF；随机标记的受限created容器确认真实101/multiplexed-stream握手，随后既有清理服务重新核对身份/created状态并删除、查询缺失。容器未启动；不宣称已验证真实命令输出或订阅就绪时序。临时socket目录自动移除。

只读Docker版本查询：Docker Desktop 4.88.1，Engine 29.7.2，API最大1.55/最小1.40，包含固定1.45。兼容性结论仅针对本机当前版本；其他部署未适配。无数据库/浏览器/模型/全量回归，无提交推送。代写授权仅本课，下一课恢复学习者实现核心。


## 启动前订阅与执行收尾协调（2026-09-21）

用户授权教练实现本课。新增 `apps/api/app/services/runtime/sandbox/sandbox_execution.py` 和对应测试。冻结并重新验证请求，在attach前和start前分别核对同一完整ID/身份/执行及隔离策略；订阅握手后启动读取任务，再发送唯一start请求。TaskGroup持有读取任务，与退出查询并行防止输出背压；只有输出EOF及严格退出事实均确认、连接关闭完成后才返回不可变结果。非零退出、OOM和daemon错误分别保留，不将退出码7误报为传输失败。耗时是本次编排耗时，不是精确进程时长。

30秒总预算，失败/超时/取消先回收读取任务及连接，尝试启动后再按原身份停止并确认。重复取消等待shield停止任务；错误保留token/ID/start_attempted/stop_confirmed。未尝试启动不停止目标，不返回部分成功、不重试start、不删除容器；失败关闭或停止未确认不能包装为成功。CLI/连接/停止收尾可超出预算，属于合作式等待边界，不承诺硬截止。仍未装配统一命令执行器或注册模型Shell工具。

从 `apps/api` 执行：

```bash
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/sandbox/test_sandbox_execution.py tests/runtime/sandbox/test_sandbox_stop.py tests/runtime/sandbox/test_sandbox_exit.py tests/runtime/docker/test_docker_client.py tests/runtime/docker/test_docker_attach_connection.py tests/runtime/docker/test_docker_attach_http.py tests/runtime/docker/test_docker_attach_stream.py
RUN_SANDBOX_EXECUTION_DOCKER=1 ../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/sandbox/test_sandbox_execution.py -k real
../../.venv/bin/python -m ruff check app/services/runtime/sandbox/sandbox_execution.py tests/runtime/sandbox/test_sandbox_execution.py
```

常规本课新增44条，直接相关合计377条通过、11条显式集成跳过（2.00s）。覆盖订阅/读取先于启动、前后策略拒绝、响应丢失、不完整/错误流、正常及非零/OOM/daemon错误结果、EOF前不返回、前置和运行中超时/取消、重复取消等待停止、原请求修改隔离、错身份/异常状态/非法退出码、连接关闭失败与无遗留任务。定向Ruff/git diff --check通过。

5条真实Docker验证通过（6.63s）：快速退出的首个stdout和stderr均完整；退出码7保留；两路各200000字节输出分别只保存65536且继续排空；忽略SIGTERM的长命令在超时和读到ready后的取消场景停止确认。每个随机标记容器结束后重新核对身份和停止状态，再非强制rm并查询缺失；该测试清理不放宽生产created-only清理契约。无数据库、浏览器、模型或全量回归，未提交推送。

时序依据：公开Moby源码的 [ContainerAttach](https://github.com/moby/moby/blob/master/daemon/attach.go) 先调用AttachStreams再GetStreams；[AttachStreams](https://github.com/moby/moby/blob/master/daemon/internal/stream/attach.go) 注册输出管道；[HTTP路由](https://github.com/moby/moby/blob/master/daemon/server/router/container/container_routes.go) 的GetStreams发送升级响应。公开v29.7.2源码URL不可用，因此不把master当作本机29.7.2二进制的逐字证据；适用范围由本机真实测试补充，未声称覆盖其他daemon实现。正常EOF/帧完整也不能排除在完整帧边界的异常截断，未承诺所有故障下输出绝对完整。


## 已退出容器的显式清理（2026-09-21）

用户授权教练实现本课。sandbox_cleanup.py新增cleanup_exited_sandbox，保留原cleanup_created_sandbox逻辑。首次await前冻结并重校验请求，以完整ID重新inspect并核对token/名称/批准镜像与一致exited/Pid=0/Running=False/Paused=False/Restarting=False/Dead=False。非强制rm回执核对后成功查询缺失才返回不可变完成快照；非零退出也允许清理，清理不判断命令业务成功。没有自动停止、重试、按名称删除或把目标已不存在当成功。取消以SandboxCleanupCancelled传播并携带token/ID/delete_attempted，删除尝试标志放在await之前。

从 `apps/api` 执行：

```bash
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/sandbox/test_sandbox_exited_cleanup.py tests/runtime/sandbox/test_sandbox_cleanup.py tests/runtime/docker/test_docker_client.py
RUN_EXITED_CLEANUP_DOCKER=1 ../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/sandbox/test_sandbox_exited_cleanup.py -k real
../../.venv/bin/python -m ruff check app/services/runtime/sandbox/sandbox_cleanup.py tests/runtime/sandbox/test_sandbox_exited_cleanup.py
```

新增50条常规，连同旧清理41条及客户端33条共124条通过、6条显式集成跳过（1.09s）。覆盖非零退出可清理、状态矛盾/未知/created/running拒绝、完整身份边界、输入错误无外部调用、分阶段失败/取消不继续操作、错误删除回执、删除后仍存在、请求快照与旧created入口不放宽。两文件Ruff及git diff --check通过。

4条真实Docker验证通过（1.45s）：随机受限容器执行后退出码7，正常删除、删除已成功后模拟响应丢失、缺失查询失败及取消均按契约返回。先验证同容器错误token不会删除；清理后实际查询缺失，重复请求不再次rm。每轮最终确认目标不存在，无残留。后三种为真实删除配合受控异常注入，不声称制造真实网络故障。无数据库/浏览器/模型/全量回归，无提交推送。

查询/删除非原子，不保证阻止宿主高权限主体并发重启或修改；非强制删除提供运行中拒绝边界。此函数未接入执行器自动清理；下一课核对并适配CommandResult表达，代写授权仅本课。


## 执行结果到 CommandResult 的适配（2026-09-21）

学习者核心与参考一致，无需修改。CommandResult新增oom_killed/daemon_error可空严格布尔字段；None表示未知，start_failed不允许携带退出错误事实。succeeded仅在exited、退出码0且两项明确False时成立，不序列化、不接受外部指定。sandbox_command_result.py仅映射内部已确认结果，Docker退出码额外限定严格整数0～255；原通用契约仍保留负数进程返回码能力。输出、双路截断、耗时原样保留，内部token/容器身份不进入公开结果；异常及清理结果不适配为已完成命令。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/sandbox/test_sandbox_command_result.py tests/runtime/command/test_command_contracts.py tests/runtime/command/test_command_output.py
../../.venv/bin/python -m ruff check app/services/runtime/command/command_contracts.py app/services/runtime/sandbox/sandbox_command_result.py tests/runtime/command/test_command_contracts.py tests/runtime/sandbox/test_sandbox_command_result.py
```

新增67条（契约35、适配器32），直接相关共228条通过（0.19s，-W error）。覆盖三态成功组合、非零退出、严格字段与不可变结果、JSON往返、两路捕获/展示/双重截断、Docker退出码边界、身份不泄露，以及停止已确认仍不能把异常转换为完整结果。教练测试的dict()格式提示修正后定向Ruff通过，git diff --check通过。

仅纯内存验证，无真实Docker、数据库、浏览器、模型或全量回归；不以本次测试宣称统一生命周期已装配。下一课创建、执行与清理的统一编排。未提交推送。


## 创建、执行与清理的统一编排（2026-09-21）

sandbox_command.py学习者核心与参考一致，无需修改。新增内部统一入口，复制请求并生成服务端token，依次创建、执行、结果适配、清理；正常非零退出同样清理。命令结果与清理凭据独立，各阶段错误及取消保留恢复证据；清理失败保留已确认命令结果，不自动重试或失败删除。未增加整个入口的硬超时承诺，恢复身份仍未持久化。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/sandbox/test_sandbox_command.py tests/runtime/sandbox/test_sandbox_command_result.py tests/runtime/sandbox/test_sandbox_creation.py tests/runtime/sandbox/test_sandbox_execution.py tests/runtime/sandbox/test_sandbox_exited_cleanup.py
../../.venv/bin/python -m ruff check app/services/runtime/sandbox/sandbox_command.py tests/runtime/sandbox/test_sandbox_command.py
RUN_SANDBOX_COMMAND_DOCKER=1 ../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/sandbox/test_sandbox_command.py -k real_create_execute_cleanup
```

新增33条常规，直接相关192条通过（0.45s，13条真实专项默认跳过）。覆盖阶段顺序、非零退出仍清理、请求复制、token独立、前置校验无外部调用、阶段错误与实际任务取消、执行停止证据、清理取消删除证据、清理失败保留命令结果、未知事实不猜测、不可变快照及安全异常。定向Ruff及git diff --check通过。

本课2条真实Docker专项通过（0.68s）：受限临时容器完整创建/执行/适配/清理，分别返回退出码0和7，输出符合预期；按本轮完整ID查询确认缺失，无临时容器残留。失败/取消分支由本课受控测试及执行/清理既有专项覆盖，本次未重新运行所有真实故障测试。无数据库、浏览器、模型或全量回归，未提交推送。下一课失败执行的只读状态核对。

## 失败执行的只读状态核对（2026-09-21）

用户授权教练实现sandbox_command_reconciliation.py。内部入口复制原请求并验证恢复token/名称/完整ID；已知ID仅在成功缺失查询后返回absent，存在时再inspect并复用严格身份/状态校验。未知ID仅允许创建阶段，复用created-only核对后按发现的完整ID重新查询。不执行创建、启动、停止、删除或重试，不补造命令结果；取消保留原恢复证据。快照不代表持续状态、清理完成或后续操作授权，内部恢复信息不输出给模型。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/sandbox/test_sandbox_command_reconciliation.py tests/runtime/sandbox/test_sandbox_reconciliation.py tests/runtime/sandbox/test_sandbox_stop.py
../../.venv/bin/python -m ruff check app/services/runtime/sandbox/sandbox_command_reconciliation.py tests/runtime/sandbox/test_sandbox_command_reconciliation.py
RUN_SANDBOX_COMMAND_RECONCILIATION_DOCKER=1 ../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/sandbox/test_sandbox_command_reconciliation.py -k real_readonly
```

新增36条常规。分批验证：首批31条连同既有核对/停止共134条通过（0.17s，2条真实专项跳过）；追加请求复制及输入校验5条后，新文件36条通过（0.08s，1条真实专项跳过）。覆盖底层Docker只读命令白名单、历史阶段与当前状态独立、完整ID约束、created-only未知ID发现、身份/不一致状态拒绝、缺失与查询失败区分、取消等待及恢复证据、请求复制和不可变结果。定向Ruff及git diff --check通过。

1条真实Docker专项通过（0.55s）：本轮受限临时容器created状态已知/未知ID核对、启动后running核对、停止后exited核对、显式清理后absent核对。写操作仅由测试安排状态及收尾，核对服务只查询；最后按完整ID确认无残留。未运行数据库、浏览器、模型或全量回归，未提交推送。本课代写授权不延续，下一课受限命令工具适配与安全错误映射。


## 受限命令工具适配与安全错误映射（2026-09-21）

用户授权实现tools/run_command.py及errors.py固定安全分类。RunCommandArguments复用CommandRequest，不另建Schema；当前沙箱路径/工作目录纯校验先于外部执行。成功只返回公开命令JSON，非零退出/截断保留。创建、执行、超时、适配、清理失败映射固定文案，内部异常保留recovery及历史command，未知错误不泄漏正文，取消原样传播。纯规格校验使用固定占位token但不执行外部调用，真实身份仍由统一入口生成。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/tools/test_run_command.py tests/runtime/sandbox/test_sandbox_command.py
../../.venv/bin/python -m ruff check app/tools/run_command.py app/tools/errors.py tests/tools/test_run_command.py
```

新增24条，连同统一入口共57条通过（0.12s，2条真实专项跳过）。覆盖公开字段选择、非零退出及截断、阶段分类与恢复证据保留、输入拒绝无执行、私有参数Schema拒绝、安全异常与原取消传播。Ruff及git diff --check通过。未调用Docker、数据库、浏览器或模型，未全量回归、提交或推送。

当前注册/调度面向同步执行器，本课不注册异步适配器，模型尚不能调用。下一课异步工具调度与受限命令注册，需明确恢复证据接收责任及可见范围；不把本课算作模型调用闭环。


## 工具定义的异步执行支持（2026-09-21）

学习者ToolDefinition实现与参考一致，无需修改。同步executor和async_executor互斥，保留旧同步位置参数构造；共用参数/上下文边界，两入口分别校验字符串结果。execute_async直接等待执行器，保留异常及取消；没有修改Runtime线程调度、工具列表或命令注册。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/tools/test_tool_definition_async.py tests/tools/test_time_tool.py tests/runtime/agent/test_agent_runtime.py tests/runtime/agent/test_agent_runtime_events.py tests/runtime/agent/test_tool_context_dispatch.py tests/runtime/agent/test_agent_execution_threads.py
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/agent/test_agent_execution_threads.py -k real_slot
../../.venv/bin/python -m ruff check app/tools/registry.py tests/tools/test_tool_definition_async.py
```

新增39条：执行器互斥/可调用性、旧构造兼容、同步及异步结果、错误入口拒绝、context缺失/别名/额外字段注入拒绝、Schema不变、错误对象与取消原样传播、当前任务直接等待及取消后的finally收尾、返回Awaitable的普通可调用对象支持。

直接相关分批104条通过。首轮102条通过（1.56s）后，PostgreSQL夹具因沙箱禁止连接127.0.0.1:5432中断；放行连接后只重跑剩余两条real_slot测试，2条通过（1.08s，10条未选中）。夹具创建随机独立测试库/私有schema，允许真实提交并自动清理，未访问开发业务表。定向Ruff及git diff --check通过。无Docker、浏览器、模型或全量回归，未提交推送。下一课Runtime同步/异步工具分派，命令注册继续待接入。


## Runtime 同步/异步工具分派（2026-09-21）

学习者改动与参考一致，无需修改。Runtime按is_async选择execute_async或原线程执行入口，统一wait_for及已有成功/错误/耗时协议。没有将异步工具放入线程跟踪器，也没有正式注册run_command。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/agent/test_async_tool_dispatch.py tests/runtime/agent/test_agent_runtime.py tests/runtime/agent/test_agent_runtime_events.py tests/runtime/agent/test_tool_context_dispatch.py tests/runtime/agent/test_agent_execution_threads.py -k 'not real_slot'
../../.venv/bin/python -m ruff check app/services/runtime/agent/agent_runtime.py tests/runtime/agent/test_async_tool_dispatch.py
```

新增28条，直接相关82条通过（1.32s，2条real_slot数据库专项未选中）。覆盖流式/非流式、有/无线程跟踪器、事件循环中执行且拒绝to_thread、成功/安全错误/未知错误/错误返回类型、参数与上下文前置拒绝、服务端context透传、超时/外部取消等待执行器finally、执行器主动取消不转Observation。同步线程成功/失败/超时/取消及跟踪器所有权旧用例同时通过。

Ruff及git diff --check通过。仅受控工具与模型决策，未调用真实模型、Docker、数据库或浏览器，未全量回归、提交或推送。异步收尾依赖执行器合作，未承诺抵御吞取消或任意后台任务。下一课命令工具内部恢复证据接收，尤其在wait_for将超时取消转为TimeoutError之前保留证据；之后再注册命令工具。


## 命令工具内部恢复证据接收（2026-09-21）

学习者核心与参考一致，无需修改。CommandRecoveryJournal固定16次尝试、执行前预留、不可变请求快照、关闭后允许在途收尾；run_command要求显式服务端记录容器，在错误/取消继续传播之前保存恢复证据。模型Schema不包含记录容器，未注册命令工具。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/sandbox/test_command_recovery_journal.py tests/tools/test_run_command.py tests/runtime/agent/test_async_tool_dispatch.py
../../.venv/bin/python -m ruff check app/services/runtime/sandbox/command_recovery_journal.py app/tools/run_command.py app/tools/errors.py tests/runtime/sandbox/test_command_recovery_journal.py tests/tools/test_run_command.py
```

新增34条，更新旧适配器测试后直接相关86条通过（0.93s）。覆盖容量/关闭/重复终态/非法索引、请求快照及独立重建、预留失败无执行、并发17次最多16次启动、记录容器隔离、成功及非零退出完成、已知/未知错误与取消证据、wait_for及外部取消后记录仍保留。教练测试首轮对取消子类转换的假设错误，随后核对本机Python 3.12.13标准库Timeout.__aexit__的精确类型检查，补齐普通与自定义取消两种路径；旧测试辅助函数同名遮蔽也已修正。生产核心未改。

本机普通CancelledError在wait_for预算耗尽时转TimeoutError；SandboxCommandCancelled仍传播，但其恢复身份已记录。下一课需修复Runtime的超时/取消来源分类后再注册，不把该版本现象泛化到所有Python版本。Ruff及git diff --check通过，无Docker、数据库、浏览器、模型或全量回归，未提交推送。


## 异步工具超时与取消分类（2026-09-21）

用户授权教练实现agent/tool_wait.py并替换Runtime等待边界。asyncio.wait只报告完成情况，自身预算耗尽使用专用ToolWaitTimeout；执行器内部TimeoutError不再误报Runtime超时。取消拥有的任务一次，gather(return_exceptions=True)+shield等待收尾并观察异常，重复外部取消不再次取消子任务；外部取消优先于超时。超时后返回或抛收尾异常均不冒充预算内成功，恢复证据由已有适配器保存。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/agent/test_tool_wait.py tests/runtime/agent/test_async_tool_dispatch.py tests/runtime/agent/test_agent_runtime.py tests/runtime/agent/test_agent_execution_threads.py -k 'not real_slot'
../../.venv/bin/python -m ruff check app/services/runtime/agent/tool_wait.py app/services/runtime/agent/agent_runtime.py tests/runtime/agent/test_tool_wait.py
```

新增14条，直接相关68条通过（1.50s，2条数据库占用专项未选中）。覆盖普通/自定义取消、吞取消返回、收尾异常、执行器异常对象保留、外部取消及重复取消等待、流式/非流式真实适配器与journal组合：Runtime预算超时生成tool_timeout且恢复身份仍保存，内部TimeoutError生成安全执行失败。同步线程兼容与既有异步协议回归同时通过。

Ruff及git diff --check通过，无Docker、数据库、浏览器、真实模型或全量回归，未提交推送。合作式收尾仍可能超出预算，不承诺强制终止永不结束的任务。下一课命令恢复记录作用域装配；命令工具仍未注册，本课代写授权不延续。


## 命令恢复记录作用域装配（2026-09-21）

学习者实现CommandRecoveryStore及main.lifespan/ChatExecution装配与参考一致，仅补execute_command捕获Run创建异常的Ruff说明（参考遗漏），业务逻辑不变。应用最多保存32运行，每次journal最多16命令；首次执行登记，服务端绑定身份/Run并注入，关闭后异常/取消/pending仍由应用保存，正常完成/空记录释放。没有公开恢复接口、自动淘汰或持久化，未注册命令工具。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/runtime/execution/test_command_recovery_store.py
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/chat/test_chat_execution_lifecycle.py tests/chat/test_chat_execution_budget.py
../../.venv/bin/python -m ruff check app/services/runtime/execution/command_recovery_store.py app/routers/chat/chat_execution.py app/main.py tests/runtime/execution/test_command_recovery_store.py tests/chat
```

新增29条分批通过：首批27条（1.30s），追加依赖注入/缺失拒绝2条（1.03s）。覆盖归属、身份校验、满容量及空记录回收、外来scope拒绝、关闭后的证据保留、请求绑定与重用、缺少/未完成/失败/取消Run拒绝、应用生命周期及启动/关闭失败清理。聊天生命周期和容量专项18条通过（2.48s），合计47条；使用原随机隔离PostgreSQL测试库/私有schema并自动清理，不访问开发业务表。

补齐6个绕过lifespan的旧聊天测试文件的应用state装配；账号相关文件仅机械增加存储，不推进账号专项回归。Ruff及git diff --check通过，无真实Docker、浏览器、模型或全量回归，未提交推送。下一课请求级命令工具注册与能力开放。


## 请求级命令工具注册与能力开放（2026-09-21）

学习者五文件核心与参考一致，无需修改。tools_for_execution生成请求独立能力tuple，run_command绑定当前ChatExecution.execute_command；DeepSeekDecisionMaker与Runtime共用同一快照，全局注册表不变。local流式聊天经过任务授权后开放，无上下文/非local不开放；普通/chat仍不执行Agent工具。模型参数无内部context/journal/身份；容器不挂载项目，不能宣称已能修改宿主项目。

```bash
cd apps/api
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/tools/test_request_command_tools.py tests/model/test_model_decision.py tests/runtime/agent/test_agent_runtime.py tests/runtime/agent/test_async_tool_dispatch.py
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/chat/test_chat_tool_context.py -k 'not real_context'
../../.venv/bin/python -W error -m pytest -xq --tb=short tests/chat/test_chat_execution_lifecycle.py tests/chat/test_chat_execution_budget.py tests/chat/test_chat_tool_context.py -k 'real_context or not test_chat_tool_context'
RUN_REQUEST_COMMAND_DOCKER=1 ../../.venv/bin/python -W error -m pytest -xq --tb=short tests/tools/test_request_command_tools.py -k real_docker
```

新增14条常规（请求工具12、聊天装配2）及2条真实Docker。相关常规分批94条通过：67条1.31s、7条0.05s、20条2.63s。覆盖模型往返、成功/非零退出/清理未确认/超时/取消恢复证据、请求绑定互不串用、context对象核对、公开Schema/Observation不泄漏内部身份、空集合及默认兼容、重复工具拒绝、模型和Runtime同tuple、非local不开放的能力边界。旧断连测试从全局注册表注入受控工具导致首轮超时；改为请求级工厂注入后20条数据库/请求生命周期通过，生产核心未改。使用随机隔离PostgreSQL库/schema并自动清理。

真实Docker2条通过（1.41s）：从请求绑定ToolDefinition调用实际ChatExecution/适配器/沙箱，退出码0/7、stdout符合预期，执行清理后完整ID缺失确认。模型往返测试使用受控模型；真实Docker用例直接调用请求工具，不宣称真实模型或浏览器闭环。受影响文件Ruff及git diff --check通过，无浏览器/真实模型/全量回归，未提交推送。下一课PC闭环验收。


## 受限命令工具 PC 闭环验收（2026-09-21）

新增apps/web/test/browser/command-tools.mjs与command_model.py，chat_test_app.py仅在专用场景启用受控模型与容器清理夹具。沿用run-isolated.py临时API/Next、真实迁移链及随机隔离PostgreSQL库/schema。截图发现运行已停止但工具卡片仍显示运行中，已修正chat-panel.tsx：运行终态下缺失工具结果显示“结果未确认”，不推断命令成功或容器状态。

```bash
BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=command-tools.mjs \
PLAYWRIGHT_MODULE=/Users/wanxiancheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
.venv/bin/python apps/web/test/browser/run-isolated.py
node --check apps/web/test/browser/command-tools.mjs
.venv/bin/python -m ruff check apps/web/test/browser/command_model.py apps/web/test/browser/chat_test_app.py
```

5个浏览器场景通过：键盘发送成功命令；退出码7仍作为工具结果；真实输出100000字节保存65536且stdout_truncated=true；非法相对程序路径返回command_request_rejected并继续模型解释；点击停止后204、Run持久化aborted且没有伪造TOOL_CALL_RESULT。成功三场景检查1366×900和1920×900无横向溢出，页面展示真实结果，刷新恢复答案且不新增聊天POST，运行事件仍可查询。无页面JS错误。模型决策受控，工具/授权/BFF/API/数据库/Docker均真实；不宣称真实模型推理质量。

全场景运行创建4个容器，3个正常执行后自动清理；取消记录在应用存储保留cancelled/start_attempted=True/stop_confirmed=True，测试夹具按原身份核对并清理剩余1个，4个完整ID均成功查询缺失。证据/private/tmp/agent-command-browser-full-evidence.json；截图/private/tmp/agent-ui-preview/output/playwright/command-{success,nonzero,large}-{1366,1920}.png及command-cancel.png。已视查成功/大输出/取消截图。临时服务、随机测试库/schema已清理，未触及开发业务表。

实际界面仍通用展示JSON，大输出导致详情过长；本课验证协议/布局及持久化，不将现有显示称为最终命令卡片。下一课结构化展示退出码、错误事实、截断及有界输出区域。未运行全量回归或真实模型，未提交推送。

取消详情补验：加BROWSER_SCENARIO=cancel只重跑停止场景，展开运行详情后断言“已停止生成”实际可见，通过后刷新核对aborted。该轮另建1个容器，恢复记录同样为cancelled/start_attempted=True/stop_confirmed=True，夹具清理1个并确认缺失；临时服务及测试库/schema再次清理。证据/private/tmp/agent-command-browser-evidence.json，command-cancel.png已更新。

取消工具文案修复后再次仅重跑取消场景通过：展开详情实际可见“已停止生成”和“结果未确认”，更新截图已视查；该轮额外1容器按身份清理并确认缺失，临时服务/数据库清理。整个验收共创建6个临时容器（全场景4、两次取消补验各1），均查询确认无残留。chat-panel.tsx定向ESLint和tsc --noEmit通过，浏览器脚本语法、Python夹具Ruff及diff check通过。


## 命令结果结构化展示验收（2026-09-21）

新增command-result-view.ts与components/tool-result.tsx，chat-panel按工具名路由结果。只在公开事实充分时显示命令成功；非零退出、OOM/daemon错误为失败，未知事实保留未知。超时/取消不因退出码0改判成功；启动失败显示固定分类。每路输出独立截断提示、192px限高及键盘焦点，React纯文本转义；其他工具/异常协议使用通用限高文本区。

从apps/web执行：

```bash
node --experimental-strip-types --test test/features/chat/command-result.test.ts test/features/chat/run-summary-card.test.ts test/features/chat/tool-duration-view.test.ts
pnpm exec tsc --noEmit
pnpm exec eslint src/features/chat/command-result-view.ts src/features/chat/components/tool-result.tsx src/features/chat/components/chat-panel.tsx
```

新增30条专项，连同直接相关摘要/耗时共48条通过（1.04s）。覆盖四种命令状态、退出码/错误事实、未知兼容、错误类型/矛盾字段、Unicode边界、普通工具回退、HTML转义、独立通道和截断。tsc、定向ESLint、浏览器脚本语法及git diff --check通过。

沿用上一节完整浏览器启动命令，5场景全部通过：成功、非零退出、10万字节输出截断、参数拒绝及取消。新增断言结构化状态、调用完成文案、stdout真实内容/192px限高/可滚动/键盘焦点、stderr空输出，1366×900和1920×900无横向溢出。刷新不重执行、持久化事件及停止后结果未确认保持通过，无页面JS错误。成功与大输出截图已视查，路径仍为/private/tmp/agent-ui-preview/output/playwright/command-{success,nonzero,large}-{1366,1920}.png。

本轮4个真实容器全部确认缺失，取消恢复记录cancelled/start_attempted=True/stop_confirmed=True，夹具按身份清理1个。证据/private/tmp/agent-command-browser-evidence.json；临时API/Next及隔离PostgreSQL库/schema清理完成。模型决策受控，无真实模型或全量回归，未提交推送。OOM/daemon未知与异常协议通过单元/真实组件渲染验证，不宣称真实Docker故障注入。

## 受限跨目录文件查找验收（2026-09-21）

学习者新增workspace_find.py与参考一致，未修改核心实现。教练新增tests/workspace/test_workspace_find.py，共55条专项；复用既有目录资源跟踪夹具和根PostgreSQL隔离夹具。

从apps/api执行：

```bash
../../.venv/bin/python -W error -m pytest -q tests/workspace/test_workspace_find.py tests/workspace/test_workspace_path.py tests/workspace/test_workspace_listing.py
../../.venv/bin/python -W error -m pytest -q tests/workspace/test_workspace_find.py -k 'start_directory or fstat_failure or exact_path'
../../.venv/bin/python -m ruff check app/services/workspace/workspace_find.py tests/workspace/test_workspace_find.py
```

首次纯文件系统45条通过（1.66s，5条数据库用例未选）；随后新文件首批50条连同直接相关路径63/枚举26，共139条通过（2.69s）。追加5条起始路径替换/消失、子fd打开后fstat失败、路径长度精确边界后单独运行通过（0.74s）。最终新增55条、相关合计144条分批通过，未重复全量回归；上方第一条命令在最终文件状态会收集144条。

覆盖字面量/大小写/Unicode/空格、文件名而非路径匹配、有限排序、独立调用、50/51匹配和2000/2001扫描、跨层全局预算、8/9层及兄弟分支继续、路径限额、链接/断链/FIFO跳过、无正文读取、目录替换/消失/元信息变化、open/scandir/stat/fstat失败时描述符及迭代器清理、平台能力不足拒绝和安全错误。权限与I/O错误用故障注入；文件/目录/符号链接与描述符使用真实临时文件系统。

5条数据库用例使用随机隔离PostgreSQL库及私有schema：已授权查询仅SELECT，Session在扫描前关闭；他人任务、未绑定目录、外部链接和上级路径在扫描前拒绝。测试正常退出，既有夹具自动删除本轮schema/数据库，无开发业务表写入。Ruff及git diff --check通过。

本课仅只读服务，未注册模型工具；无浏览器、Docker、真实模型或全量回归，未提交推送。目录版本核对不构成原子快照或挂载隔离；固定扫描量不提供硬性I/O超时。下一课文件查找工具适配与注册。

## 文件查找工具适配与注册验收（2026-09-21）

学习者find_files.py、errors.py、registry.py核心与参考一致，无需修改。新增tests/tools/test_find_files_tool.py 54条，更新旧test_read_file_tool.py的完整能力集合预期，加入find_files。

从apps/api执行：

```bash
../../.venv/bin/python -W error -m pytest -q tests/tools/test_find_files_tool.py tests/tools/test_search_file_tool.py tests/tools/test_read_file_tool.py tests/tools/test_request_command_tools.py -k 'not real_docker'
../../.venv/bin/python -W error -m pytest -q tests/tools/test_read_file_tool.py -k capability_filter
../../.venv/bin/python -m ruff check app/tools/find_files.py app/tools/registry.py app/tools/errors.py tests/tools/test_find_files_tool.py tests/tools/test_read_file_tool.py
```

首批147条通过、1条失败（1.39s，2条真实Docker未选）：旧测试写死原能力集合，没有包含新注册的find_files。首次修正命令误用相对Python路径，未实际修改，重跑仍同样失败；改用正确解释器路径修正测试后，该组3条通过（0.71s）。最终直接相关148个不同用例分批通过，新工具54条均首轮通过。无生产核心修改，Ruff及git diff --check通过。

覆盖严格类型、query必填/目录默认、长度与分隔符拒绝、身份/预算注入拒绝、Schema唯一来源、可信上下文透传、空及截断结果、全部安全错误/未来分类回退、未知异常与取消传播、无context可见性及直接执行拒绝、请求能力集合保留旧工具。受控DeepSeek客户端和Runtime使用同一能力tuple，验证查找→读取及空/截断/已知失败/未知失败/非法参数/无context共7条模型往返；底层文件服务模拟，无真实文件扫描或数据库访问。

未调用真实模型、数据库、Docker或浏览器，未全量回归，未提交推送。下一课使用真实临时目录及隔离数据库进行PC浏览器闭环验收。

## 文件查找 PC 闭环验收（2026-09-21）

新增apps/web/test/browser/find-tools.mjs、find_model.py，chat_test_app.py按[find-*]标记使用受控决策。无生产代码改动。查找和读取结果来自真实服务，受控模型消费paths后请求读取，并根据实际truncated/错误Observation生成回答；不预填工具输出。

仓库根运行：

```bash
BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=find-tools.mjs \
PLAYWRIGHT_MODULE=/Users/wanxiancheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
.venv/bin/python apps/web/test/browser/run-isolated.py
node --check apps/web/test/browser/find-tools.mjs
.venv/bin/python -m ruff check apps/web/test/browser/find_model.py apps/web/test/browser/chat_test_app.py
```

5场景全部通过：嵌套中文文件查找→读取真实内容；完整空匹配；51个匹配文件仅返回50且scanned_entries=51/truncated=true；上级路径拒绝workspace_path_rejected；未绑定项目拒绝workspace_directory_unbound。每场景经键盘发送、真实BFF/API/授权/文件服务/数据库，检查工具调用顺序、结果/错误事件、唯一RUN_FINISHED。每场景1366×900和1920×900无横向溢出，刷新不新增聊天POST，历史答案和Run事件持久化核对通过，无页面JS错误。

截图/private/tmp/agent-ui-preview/output/playwright/find-{success,empty,truncated,escape,unbound}-{1366,1920}.png，已视查成功1366、截断1920及越界1366。工具详情仍为有界JSON文本，截断解释来自受控模型，不宣称新增专用结果卡片或真实模型措辞质量验收。

真实临时项目位于/private/tmp/agent-find-*，测试后自动删除；嵌套源文件和外部哨兵内容保持不变，页面及事件无绝对项目路径或外部秘密。启动器正常退出，临时API/Next停止，随机隔离PostgreSQL库/schema清理。脚本语法、Ruff及git diff --check通过；未运行Docker、真实模型或全量回归，未提交推送。下一课单文件文本替换预览，先做纯内存计算与Diff，不执行文件写入。

## 单文件文本替换预览验收（2026-09-21）

学习者workspace_edit_preview.py与参考一致，未修改核心。新增tests/workspace/test_workspace_edit_preview.py 65条纯内存专项。

从apps/api执行：

```bash
../../.venv/bin/python -W error -m pytest -q tests/workspace/test_workspace_edit_preview.py
../../.venv/bin/python -W error -m pytest -q tests/workspace/test_workspace_edit_preview.py -k 'invalid_text or line_endings'
../../.venv/bin/python -m ruff check app/services/workspace/workspace_edit_preview.py tests/workspace/test_workspace_edit_preview.py
```

65条通过（0.11s）。Ruff对测试中两个不同孤立代理项的转义字面量误报PT014，改用chr(0xD800)/chr(0xDFFF)明确构造；另把换行断言拆为可读变量，相关25条复验通过（0.06s）。最终Ruff及git diff --check通过。

覆盖精确首/中/末替换、删除全部、跨行替换、字面量特殊字符、Unicode字节计数、三个字段类型/NUL/孤立代理项拒绝、空旧文本/无变化/缺失/多处及重叠匹配拒绝、LF/CR/CRLF与混合换行/末尾无换行、JSON行回解、三字段256KiB字节超限与精确上限、修改后超限、4000行精确/前后超限、Diff字符及生成片段边界/默认截断、完整新内容保留、不可变与独立重复调用。测试禁止普通文件打开，未访问文件系统、数据库、模型、Docker或浏览器；无全量回归，未提交推送。

审阅Diff使用JSON行表示，不能直接git apply；截断展示不是完整审批依据。下一课授权文件预览与基线摘要，仍不执行写入。

## 授权文件预览与基线摘要验收（2026-09-21）

学习者workspace_file_preview.py与参考一致，未修改核心；新增tests/workspace/test_workspace_file_preview.py 28条。

从apps/api执行：

```bash
../../.venv/bin/python -W error -m pytest -q tests/workspace/test_workspace_file_preview.py tests/workspace/test_workspace_edit_preview.py tests/workspace/test_workspace_file.py
../../.venv/bin/python -m ruff check app/services/workspace/workspace_file_preview.py tests/workspace/test_workspace_file_preview.py
```

新增28条连同预览65/读取35，共128条通过（1.89s，-W error）；Ruff及git diff --check通过。覆盖身份参数透传、规范相对路径、单次读取、原文而非修改后SHA-256、UTF-8/中文/emoji/BOM/CR/LF/CRLF、嵌套不可变结果、截断Diff保留完整新内容、授权/目录/路径/读取/未知异常/取消原样传播，以及替换失败不生成摘要。

真实文件和隔离PostgreSQL验证读取后Session关闭且查询仅SELECT，正常预览文件字节不变；另由测试显式模拟外部编辑器在读取后改文件，返回预览与摘要仍对应原始同次读取，服务不覆盖外部内容。他人任务、未绑定、外部链接、缺失、非法UTF-8及歧义替换拒绝，文件保持原字节。数据库/schema由根夹具自动清理；没有写入开发业务表。

本课不新增文件写入、审批或实际冲突检测；SHA-256只作为后续内容基线。无Docker、浏览器、真实模型或全量回归，未提交推送。下一课文件修改预览工具适配与注册。

## 文件修改预览工具验收（2026-09-21）

学习者preview_file_edit.py及registry.py核心与参考一致；errors.py遗漏8项固定文案，教练补齐invalid_edit_text/edit_text_too_large/empty_old_text/edit_no_change/edit_target_not_found/edit_target_ambiguous/edit_preview_too_many_lines/edit_preview_unavailable，否则服务失败会触发未知安全错误码ValueError。另更新旧read_file能力集合断言包含preview_file_edit。

从apps/api运行：

```bash
../../.venv/bin/python -W error -m pytest -q tests/tools/test_preview_file_edit_tool.py tests/tools/test_read_file_tool.py tests/tools/test_find_files_tool.py tests/tools/test_request_command_tools.py -k 'not real_docker'
../../.venv/bin/python -m ruff check app/tools/preview_file_edit.py app/tools/errors.py app/tools/registry.py tests/tools/test_preview_file_edit_tool.py tests/tools/test_read_file_tool.py
```

新增69条专项与相关共172条通过（1.36s，2条真实Docker未选），Ruff及git diff --check通过。覆盖三字段必填/严格类型、额外身份/批准/基线注入拒绝、字符与UTF-8字节限额/NUL/孤立代理项、空新文本删除、原始空格换行、公开字段精确集合及完整新内容不泄漏、两种截断状态、18种安全错误映射含未来分类回退、context可见性/执行拒绝、未知异常和取消原样传播。6组受控模型/Runtime往返覆盖成功、截断、已知失败、未知失败、非法参数、无上下文；请求级能力快照一致，错误无PRIVATE哨兵，结果无完整新内容哨兵。

模型出口和底层预览服务模拟，未访问数据库、Docker、浏览器或真实模型，没有全量回归；真实授权文件只读验证沿用上一课证据，本课不宣称浏览器验收。未提交推送，下一课文件修改预览PC闭环验收。

## 文件修改预览PC闭环验收（2026-09-21）

新增apps/web/test/browser/preview-tools.mjs、preview_model.py；chat_test_app.py按标记路由受控模型决策。BFF/API/授权/预览/文件和数据库真实，未修改生产代码。

根目录运行：

```bash
BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=preview-tools.mjs \
PLAYWRIGHT_MODULE=/Users/wanxiancheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
.venv/bin/python apps/web/test/browser/run-isolated.py
node --check apps/web/test/browser/preview-tools.mjs
.venv/bin/python -m ruff check apps/web/test/browser/preview_model.py apps/web/test/browser/chat_test_app.py
```

4场景全部通过：带BOM/CRLF的成功替换预览、删除至空内容预览、旧文本多处匹配拒绝edit_target_ambiguous、长单行Diff截断为16384字符。每场景用Node crypto独立核对原文SHA-256及UTF-8前后字节数（失败场景无结果），公开字段集合精确核对为preview_only/相对路径/摘要/字节数/Diff/截断，无updated_content或宿主路径；逐场景核对所有4个源文件字节不变。模型根据真实Observation给出未写入/截断或失败文案，不替换工具服务。

每场景经过键盘发送，1366×900及1920×900无横向溢出，结果DOM与公开JSON一致且区域限高256px，刷新不新增聊天POST，历史答案及成功/错误事件保留，Run完成且仅一个RUN_FINISHED，无页面JS错误。截图/private/tmp/agent-ui-preview/output/playwright/preview-{success,delete,ambiguous,truncated}-{1366,1920}.png；已视查成功1366和截断1920。现有工具结果仍通用JSON展示，下一课做结构化预览卡片。

临时/private/tmp/agent-preview-*目录已删除，临时API/Next已停止，隔离PostgreSQL库/schema自动清理。脚本语法/Ruff及git diff --check通过，无真实模型、Docker或全量回归，未提交推送。本课证明预览链路只读，不证明审批/实际写入已经实现。

## 文件修改预览结构化展示验收（2026-09-21）

学习者file-edit-preview-view.ts、file-edit-preview-card.tsx、tool-result.tsx及chat-panel.tsx核心与参考一致，无需修改。新增file-edit-preview.test.ts 38条，抽取render-tool-result.ts供新旧卡片测试共用，编译真实TSX而非复制JSX；旧命令测试增加的新组件依赖由该夹具装配。

从apps/web运行：

```bash
node --experimental-strip-types --test test/features/chat/file-edit-preview.test.ts test/features/chat/command-result.test.ts test/features/chat/run-summary-card.test.ts
pnpm exec tsc --noEmit
pnpm exec eslint src/features/chat/file-edit-preview-view.ts src/features/chat/components/file-edit-preview-card.tsx src/features/chat/components/tool-result.tsx src/features/chat/components/chat-panel.tsx
```

82条通过（1.04s），覆盖无效/缺失字段、Unicode字符上限、零字节/字节上限、SHA格式、私有字段不传播、Diff元信息优先分类、两种截断渲染、HTML转义、JSON换行表示保留、异常回退及命令/摘要组件兼容。tsc、定向ESLint、浏览器脚本语法和git diff --check通过。

沿用上一节preview-tools.mjs完整浏览器启动命令，4场景全部通过。更新断言：仅预览/调用完成/截断状态实际可见，Diff的DOM文本与公开结果完全一致，256px限高，键盘聚焦及长Diff可滚动；SHA-256默认折叠，Enter展开后完整摘要一致，再Enter收起；删除预览显示0字节。保留真实文件字节不变、独立SHA核对、公开字段、两种PC宽度、持久化及刷新不重放断言，无页面JS错误。

截图沿用preview-{success,delete,ambiguous,truncated}-{1366,1920}.png，成功1366及截断1920已视查，布局与Diff着色正常。模型受控，授权/文件/数据库真实；临时目录、API/Next及隔离PostgreSQL库/schema已清理。没有真实模型、Docker或全量回归，未提交推送。本课无批准/应用按钮；下一课文件修改提案持久化。

## 文件修改提案持久化验收（2026-09-21）

学习者四文件核心通过验收，仅补models.py末尾换行。新增37条真实PostgreSQL专项；与任务删除、文件预览、数据库启动检查直接相关共109条分批通过：首批107条9.06s，追加双向锁竞争2条1.20s。Ruff和git diff --check通过。根夹具创建的独立数据库/schema自动清理，不运行全量回归、Docker、浏览器或真实模型。

从apps/api运行本课与直接相关验收：

```bash
../../.venv/bin/python -W error -m pytest -q tests/workspace/test_file_edit_proposal_service.py tests/migrations/test_file_edit_proposal_migration.py tests/tasks/test_task_deletion_service.py tests/workspace/test_workspace_file_preview.py tests/migrations/test_database_readiness.py
../../.venv/bin/python -m ruff check app/models.py app/repositories/workspace/file_edit_proposal_repository.py app/services/workspace/file_edit_proposal_service.py migrations/versions/4eb108c473ab_add_file_edit_proposals.py tests/workspace/test_file_edit_proposal_service.py tests/migrations/test_file_edit_proposal_migration.py
```

覆盖真实旧版升级/回退/再升级及旧业务数据保留，ORM与迁移无漂移、数据库pending默认值/约束/UTF-8字节边界/唯一索引/CASCADE；保存提交与公开不可变结果、BOM/CRLF及规范相对路径、删除至空内容、Diff截断仍存完整新内容、重复调用独立ID、flush后和commit前故障回滚、初次及保存前越权/绑定变化/任务删除拒绝、事务外文件读取、实际加锁顺序、保存/删除双向锁竞争及回滚释放。外部编辑后提案仍保留同一次读取基线，服务不覆盖文件；该证据不代表写入冲突检测已实现。

本机开发库从3da097b362fa升级至4eb108c473ab，仅新增提案表及索引；迁移事务内逐行比较既有业务表一致，check_database_ready通过，alembic check无新增迁移操作。其他电脑同步代码后仍需各自运行：

```bash
../../.venv/bin/python -m alembic upgrade head
../../.venv/bin/python -m alembic check
```

没有新增HTTP/模型入口、批准状态或文件写入；pending不等于已批准，重复请求没有幂等保证，提交结果不确定不可盲目重试。未提交推送。下一课文件修改提案授权查询。

## 文件修改提案授权查询验收（2026-09-21）

用户授权教练完成本课核心及配套。现有file_edit_proposal_repository.py新增公开列联表查询，同一SQL约束提案编号、Task、Workspace及Workspace/Conversation双方用户归属；service新增FileEditProposalDetail和get_task_file_edit_proposal，Session关闭后返回不可变普通数据。未改变模型/迁移/工具/API，也不读取项目文件。

从apps/api运行：

```bash
../../.venv/bin/python -W error -m pytest -q tests/workspace/test_file_edit_proposal_query.py tests/workspace/test_file_edit_proposal_service.py
../../.venv/bin/python -m ruff check app/repositories/workspace/file_edit_proposal_repository.py app/services/workspace/file_edit_proposal_service.py tests/workspace/test_file_edit_proposal_query.py
```

新增15条加保存服务18条，共33条通过（3.72s）；Ruff及git diff --check通过。覆盖单SELECT、无FOR UPDATE/commit/autoflush、不加载bound_root/proposed_content、公开字段精确集合与不可变结果、跨用户/项目/任务/缺失资源统一安全错误、会话缺失/归属变化/任务删除、截断Diff和摘要精确保存、成功/拒绝/数据库异常后Session清理。文件删除及目录解绑后仍返回保存的历史快照，这不表示当前文件仍满足基线，也不表示批准或可应用。提案Diff本身仍可能包含被修改文本，公开字段控制不等于内容脱敏。

使用根夹具真实PostgreSQL隔离测试库，自动清理；没有修改开发业务数据、执行迁移或全量回归，无浏览器/Docker/真实模型验证。未提交推送。下一课文件修改提案工具适配与注册；本课代写授权不延续。

## 文件修改提案工具验收（2026-09-21）

学习者create_file_edit_proposal.py、registry.py、errors.py核心与参考一致，无需修改。新增test_create_file_edit_proposal_tool.py 76条、test_file_edit_proposal_integration.py 3条；同步旧read_file工具能力集合断言。仅回归新增及直接相关部分。

从apps/api运行：

```bash
../../.venv/bin/python -W error -m pytest -q tests/tools/test_create_file_edit_proposal_tool.py tests/tools/test_preview_file_edit_tool.py tests/tools/test_read_file_tool.py tests/tools/test_request_command_tools.py -k 'not real_docker'
../../.venv/bin/python -W error -m pytest -q tests/tools/test_file_edit_proposal_integration.py tests/workspace/test_file_edit_proposal_service.py tests/workspace/test_file_edit_proposal_query.py
../../.venv/bin/python -m ruff check app/tools/create_file_edit_proposal.py app/tools/registry.py app/tools/errors.py tests/tools/test_create_file_edit_proposal_tool.py tests/tools/test_file_edit_proposal_integration.py tests/tools/test_read_file_tool.py
```

第一组194条通过（1.41s，2条Docker未选），第二组36条通过（3.79s），合计230条；Ruff及git diff --check通过。工具专项覆盖三字段必填/严格类型/UTF-8字节限制、禁止模型注入身份/批准/提案编号/摘要、删除与换行保留、公开创建回执精确集合及私有哨兵排除、21项安全错误分类/未来分类回退、取消及未知异常传播、context过滤与7组受控模型往返。数据库异常映射保存未确认，不泄露SQL或错误正文；单次调用不自动重试。

真实工具执行通过保存服务写入隔离PostgreSQL，再通过授权查询核对提案摘要；他人身份拒绝且无提案，原preview_file_edit仍为preview_only且不保存。所有场景核对原文件字节不变。根夹具自动清理测试库/schema，不写开发业务表，不执行迁移。没有浏览器、真实模型、Docker或全量回归，未提交推送。

描述中的禁止自动重试不构成后端幂等保证；同步等待超时或取消也不证明保存事务已停止。pending不等于已批准。下一课文件修改提案PC闭环验收。

## 文件修改提案PC闭环验收（2026-09-21）

新增proposal-tools.mjs/proposal_model.py，chat_test_app.py装配受控模型决策，run-isolated.py在浏览器完成后执行隔离数据库核对。无生产代码修改，不增加测试HTTP接口。数据库核对的授权查询SessionLocal显式绑定测试engine，避免启动器进程默认连接指向开发库。

根目录运行：

```bash
BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=proposal-tools.mjs \
PLAYWRIGHT_MODULE=/Users/wanxiancheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
.venv/bin/python apps/web/test/browser/run-isolated.py
node --check apps/web/test/browser/proposal-tools.mjs
.venv/bin/python -m ruff check apps/web/test/browser/proposal_model.py apps/web/test/browser/chat_test_app.py apps/web/test/browser/run-isolated.py
```

5场景全部通过：BOM/CRLF成功提案、删除至空内容提案、纯预览、歧义替换拒绝、长Diff截断提案。浏览器键盘发送经过真实BFF/API/Runtime/工具与文件服务；公开回执字段精确核对为proposal_id/status/relative_path/双SHA-256/diff_truncated/created_at，Node独立计算摘要，DOM实际显示提案编号和pending。错误安全分类、每次恰好一个终态、消息/结果或错误事件持久化均通过。

每场景1366×900与1920×900无横向溢出，刷新不新增聊天POST；逐场景核对所有5个夹具文件字节不变。浏览器结束后直接读取隔离PostgreSQL，最终恰好3条pending提案，分别核对完整新内容、双摘要、截断标记及授权查询结果；纯预览和失败场景无提案，刷新未重复创建。无页面JS错误，公开事件无宿主目录或updated_content。

截图/private/tmp/agent-ui-proposal/output/playwright/proposal-{success,delete,preview,ambiguous,truncated}-{1366,1920}.png；成功1366及截断1920已视查。结果目前仍通用JSON，下一课结构化创建回执，不添加批准/应用能力。数据库核对证据文件位于同目录evidence.json，仅含生成的测试数据。

临时agent-proposal目录删除，临时API/Next停止，隔离PostgreSQL库/schema自动清理；Ruff、脚本语法及git diff --check通过。无真实模型、Docker或全量回归，没有修改开发业务数据，未提交推送。本次不证明真实模型措辞可靠或请求幂等已实现。

## 文件修改提案回执结构化展示验收（2026-09-21）

用户授权教练完成核心：新增file-edit-proposal-view.ts和file-edit-proposal-card.tsx，tool-result.tsx接入；仅显示已校验公开回执，未知格式安全文本回退。时间保留服务端ISO及偏移，验证日历合法性；不新增批准或应用入口。render-tool-result.ts装配真实新组件，新增file-edit-proposal.test.ts 34条。

从apps/web运行：

```bash
node --experimental-strip-types --test test/features/chat/file-edit-proposal.test.ts test/features/chat/file-edit-preview.test.ts test/features/chat/command-result.test.ts
pnpm exec tsc --noEmit
pnpm exec eslint src/features/chat/file-edit-proposal-view.ts src/features/chat/components/file-edit-proposal-card.tsx src/features/chat/components/tool-result.tsx
```

102条通过（1.26s），覆盖缺失字段、类型、十六进制编号/双摘要、Unicode上限、时区/闰年/错误日期、私有字段不传播、真实TSX两种截断渲染、HTML转义及协议回退。tsc、定向ESLint、浏览器脚本语法、配套Python Ruff及git diff --check通过。

沿用上一节proposal-tools.mjs完整隔离启动命令，5场景全部通过；新增卡片区域、待审批/尚未写入、时间元素datetime及文本与回执一致、截断提示、默认摘要折叠/Enter展开核对双摘要/再次Enter收起、无操作按钮断言。保留1366/1920无溢出、真实文件不变、刷新不重复、消息事件与数据库最终3条pending等核对。模型受控；截图成功1366、截断1920已视查，路径沿用上一节。临时项目目录/API/Next/隔离PostgreSQL库schema已清理。

没有真实模型、Docker或全量回归，未提交推送。卡片展示创建时状态，不能当作当前审批状态或基线仍有效的证明。本课无新增重要考点独立入库。下一课文件修改提案详情HTTP接口。

## 文件修改提案详情HTTP验收（2026-09-21）

新增FileEditProposalDetailResponse及workspace路由GET /workspaces/{workspace_id}/tasks/{task_id}/file-edit-proposals/{proposal_id}。学习者报告status:str无法传给Literal[pending]，来自参考代码类型遗漏；教练改用model_validate显式公开字段映射，在HTTP边界验证真实状态，不cast、不硬编码pending。无独立Pyright/Pylance CLI运行，运行时未知状态拒绝由测试证实。

从apps/api运行：

```bash
../../.venv/bin/python -W error -m pytest -q tests/workspace/test_file_edit_proposal_api.py tests/workspace/test_file_edit_proposal_query.py tests/tasks/test_task_run_api.py
../../.venv/bin/python -m ruff check app/schemas.py app/routers/workspace/workspace.py tests/workspace/test_file_edit_proposal_api.py
```

新增24条，相关81条分批通过：首轮80通过、1条新测试误将local身份初始化INSERT视作业务写入；修正断言后HTTP24条通过（3.67s）。本地身份依赖执行INSERT ON CONFLICT DO NOTHING，不能声称整个GET链路只有SELECT；提案服务单次SELECT、无FOR UPDATE/commit，Session关闭验证通过。Ruff与git diff --check通过。

覆盖真实local身份和已提交提案的公开响应、伪造Cookie/header/query身份无效、三个路径标识格式422、他人项目/会话/任务和不存在统一404、任务删除后不可访问、缺失/错误凭证及恶意Host/Origin 403、运行时未知状态与内部异常脱敏500、成功和错误均no-store。文件删除及解绑仍返回历史Diff，截断原样保留，查询不改文件。数据库由根夹具隔离并清理，未修改开发业务数据。无迁移/浏览器/真实模型/全量回归，未提交推送。

接口复用WorkspaceRoute既有安全文案，不新增详情BFF或审批/文件应用接口。下一课文件修改提案详情BFF代理。

## 文件修改提案详情BFF代理验收（2026-09-21）

学习者file-edit-proposal-data.ts、file-edit-proposal-proxy.ts及file-edit-proposals/[proposalId]/route.ts与参考一致，核心无需修改。新增file-edit-proposal-route.test.ts 56条，直接导入真实Next GET函数，经代理及解析器处理；只模拟fetch上游，不启动浏览器或后端。

从apps/web运行：

```bash
node --experimental-strip-types --test test/features/workspaces/file-edit-proposal-route.test.ts test/features/workspaces/task-run-route.test.ts test/features/chat/file-edit-proposal.test.ts
pnpm exec tsc --noEmit
pnpm exec eslint src/features/workbench/file-edit-proposal-data.ts src/app/api/_shared/file-edit-proposal-proxy.ts 'src/app/api/workspaces/[workspaceId]/tasks/[taskId]/file-edit-proposals/[proposalId]/route.ts'
```

169条通过（1.02s），tsc、定向ESLint及git diff --check通过。覆盖local访问边界拒绝且不fetch、内部凭证仅取服务端配置、不透传浏览器Cookie/Authorization/伪造内部头、不复制上游Set-Cookie等头、URL精确匹配、三个路径标识与末尾换行拒绝、额外/重复查询参数拒绝、响应资源归属不匹配与非法状态/摘要/时间/Diff拒绝、公开字段投影、Unicode边界及截断原样保留、无效JSON/网络脱敏、错误流cancel、成功/错误no-store。

使用可控AbortSignal验证既定20秒预算及浏览器取消，在fetch等待、正文读取、JSON读取后均不返回成功；已取消请求不fetch，网络失败仅尝试一次。上游服务受控，不宣称真实HTTP、数据库或浏览器闭环；未运行模型或全量回归，未提交推送。下一课详情按需展示，完成后补真实PC详情查询验收。

## 文件修改提案详情按需展示验收（2026-09-21）

学习者完成详情组件、ToolResult范围透传及ChatPanel调用。参考代码遗漏selection.task可为null，教练修正为workbench.selection?.task，并仅修复该段粘贴缩进；tsc证实类型错误消除。render-tool-result.ts编译真实详情TSX并注入真实React hooks，仅按钮外观替为原生按钮；新增5条初始状态/无上下文/非法范围/未知协议测试，交互由浏览器验证。

从apps/web运行：

```bash
node --experimental-strip-types --test test/features/chat/file-edit-proposal-detail.test.ts test/features/chat/file-edit-proposal.test.ts test/features/chat/file-edit-preview.test.ts test/features/chat/command-result.test.ts test/features/workspaces/file-edit-proposal-route.test.ts
pnpm exec tsc --noEmit
pnpm exec eslint src/features/chat/components/file-edit-proposal-detail.tsx src/features/chat/components/tool-result.tsx src/features/chat/components/chat-panel.tsx
```

163条通过（1.26s），tsc/定向ESLint、浏览器脚本语法、配套Python Ruff及git diff --check通过。

沿用proposal-tools.mjs完整隔离启动命令，run-isolated.py增加复制提案详情Next GET路由。PC5场景全部通过。三个成功提案点击后走真实浏览器→BFF→FastAPI→授权查询→隔离PostgreSQL；断言详情三标识/摘要、no-store、DOM Diff与响应完全一致、公开数据无宿主目录或完整proposed_content。Diff可键盘聚焦、限高256px，截断场景可滚动，1366/1920无横向溢出；刷新不自动读详情或重复创建，数据库最终仍3条pending、所有源文件字节不变。

成功场景另外注入一次404响应，页面固定错误且无PRIVATE泄漏，手动重试恢复真实BFF读取；另暂挂旧响应，取消后发起新读取再释放旧响应，显示保持新结果。错误/延迟注入仅在这两段浏览器响应，正常保存及首次/重试读取链路真实。跨任务挂起响应尚未单独注入，key包含三标识与卸载abort/ref失效逻辑已代码核对，不把该项称为独立浏览器验收。

成功1366和截断1920截图已视查，路径沿用proposal-{marker}-{width}.png；临时项目目录、API/Next与隔离数据库/schema已清理。模型受控，无真实模型、Docker或全量回归，未提交推送。当前只有只读详情，没有批准/应用按钮；本课不新增独立样式面试题。下一课提案审批状态与决策服务。

## 2026-09-21 当日归档

用户要求停止今日学习，更新既有文档并将累计改动推送main，再创建新学习会话。归档范围为本日课程代码与配套，排除临时浏览器输出和环境凭证；各课定向验证结果见上述记录，本次不重复全量回归。收尾执行git diff --check、暂存差异检查与远端main同步核对；提交/推送事实以Git记录及本次任务结果为准。主仓库学习约定保持不变，新会话入口LEARNING_HANDOFF.md。
