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
../../.venv/bin/python -m pytest -q tests/test_login_api.py
```

2026-09-14 修正后本课 29 passed，项目 `.venv` 完整后端回归 380 passed，Ruff 通过；有 1 条既有 Starlette/AnyIO 弃用警告。输入合法性检查返回安全 422，不泄露原始密码。登录请求须包含允许的 Origin 和 application/json；目前仅验收后端，浏览器 BFF 登录链路尚未接入。

## 登录会话解析服务验收

在 `apps/api` 使用项目环境运行：

```bash
../../.venv/bin/python -m pytest -q tests/test_login_session_resolver.py
../../.venv/bin/python -m pytest -q
../../.venv/bin/python -m ruff check app tests
```

2026-09-14 本课新增 29 条测试通过，完整后端回归 409 passed，Ruff 通过；仍有 1 条既有 Starlette/AnyIO 弃用警告。数据库测试复用独立库与私有 schema，未修改开发数据或迁移。解析服务已由后续 GET /auth/me 接入。

## 当前用户 HTTP 接口验收

在 `apps/api` 使用项目环境执行：

```bash
../../.venv/bin/python -m pytest -q tests/test_current_user_api.py
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
../../.venv/bin/python -W error::DeprecationWarning -m pytest -q tests/test_logout_service.py
../../.venv/bin/python -W error::DeprecationWarning -m pytest -q
../../.venv/bin/python -m ruff check app tests
```

本课 26 passed，完整后端 451 passed、零警告，Ruff 通过。数据库测试使用独立 PostgreSQL 测试库自动清理；未修改开发数据。撤销服务已由后续 POST /auth/logout 接入并清 Cookie。

## 登出 HTTP 验收（2026-09-14）

在 apps/api 使用项目环境：

```bash
../../.venv/bin/python -W error::DeprecationWarning -m pytest -q tests/test_logout_api.py
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

新增 `apps/api/tests/test_chat_auth_boundary.py`（模型与 run 创建模拟，不访问数据库）：从 apps/api 执行 `../../.venv/bin/python -W error::DeprecationWarning -m pytest -q tests/test_chat_auth_boundary.py`，结果 11 passed、3 failed。流式装饰器遗漏 require_current_user，导致拒绝认证和认证故障场景仍返回 200，成功场景亦没有执行依赖。已向学习者提供修正段，本课尚未通过。

新增聊天 BFF 34 条测试已通过（test:auth 自动纳入）；test:state 共 76 条通过，pnpm typecheck、pnpm lint、后端 Ruff 通过。本轮未跑后端全量或浏览器；真实隔离数据库认证测试、旧断言适配与浏览器链路留待核心修正后完成。

### 2026-09-14 聊天认证课修正后整体验收

学习者补齐流式路由 Depends 后，本课通过。普通 /chat 与 /chat/stream 均在创建 run/模型调用前认证；后端 ChatRoute、BFF Cookie 转发与前端 401 提示完成。认证不等于所有权，会话仍使用匿名历史归属和 session_id 缓存键，资源隔离留到下一课。

- 后端：从 apps/api 执行 `../../.venv/bin/python -W error::DeprecationWarning -m pytest -q`，**510 passed，零警告**。新增 test_chat_auth_boundary.py 14 条与 test_chat_auth_sessions.py 18 条；后者使用现有 PostgreSQL 隔离夹具，验证有效/缺失/格式错误/未知/过期/撤销会话、SQL 故障、Session 提前关闭与输入脱敏，模型/run 创建模拟。旧测试补来源和认证前提，调整聊天错误契约断言。
- 前端：`pnpm test:auth` **136 passed**，`pnpm test:state` **76 passed**；`pnpm typecheck`、`pnpm lint` 与后端/浏览器 Python 配套 Ruff 通过。Rosetta Node 性能提示仍存在，不是 Python 弃用警告。
- 浏览器：原隔离命令运行 **20/20 场景通过**，包括新增真实登录→聊天 BFF→FastAPI NDJSON→撤销→重放旧 Cookie→401/保留输入。run-isolated.py 复制真实聊天 BFF，使用 chat_test_app.py 作为测试服务器入口，要求随机测试数据库前缀。只模拟模型决策与 Redis 等待，认证、Agent Loop、聊天服务、运行与消息持久化使用真实代码；未访问真实模型。整套浏览器超时上限为 660 秒。
- 临时 Next/FastAPI 服务和随机 PostgreSQL 数据库/schema 已自动清理，开发业务表未参与测试。学习交接、重要面试题和索引已同步。

### 会话仓储所有权原语验收

新增 require_owned_conversation / get_or_create_owned_conversation 已通过，现有聊天调用尚未切换。新增 `apps/api/tests/test_owned_conversation_repository.py` 15 条测试，复用 PostgreSQL 独立库/私有 schema；两个物理连接通过 pg_blocking_pids 确认锁等待，覆盖同/不同用户争用及首事务提交/回滚。测试按当前 READ COMMITTED 运行，不代表已实现 SERIALIZABLE 重试。

从 apps/api 执行 `../../.venv/bin/python -W error::DeprecationWarning -m pytest -q`：**525 passed，零警告**。Ruff 与前端 `pnpm lint` 通过。本课未改接口/UI，未重跑浏览器和前端单测；136/76/20 为上一课验收结果。核心无需修正，只补空行与文件末尾换行；测试验证所有权、重复复用、跨连接事务可见性、外层回滚与数据库异常分类。没有新增迁移或操作开发业务数据。

### 聊天读写、历史与缓存所有权接入验收

本课通过。身份从 CurrentUser.id 进入模型上下文、会话读写和 run 创建；缓存按 (user_id, session_id) 定位并在命中前查所有权。历史查询同步认证，自己的空会话 200，未知/他人 404。旧匿名创建函数和未使用导入清理，保留历史数据。

- 全量后端 `../../.venv/bin/python -W error::DeprecationWarning -m pytest -q`（apps/api）**537 passed，零警告**。新增 test_chat_ownership.py 12 条，真实隔离 PostgreSQL/令牌，只模拟模型与 Redis 等待。旧模型测试、run 测试及保存参数断言已适配 user_id。
- 前端 `pnpm test:auth` **137**、`pnpm test:state` **77**、`pnpm typecheck`、`pnpm lint` 通过；后端和浏览器 Python 配套 Ruff 通过。
- 用户发现静态类型错误后，历史路由改为逐条 ConversationMessage.model_validate，再构造 ConversationHistoryResponse；`npx --yes --package pyright pyright --pythonpath ../../.venv/bin/python app/routers/conversation.py`（apps/api）**0 errors/0 warnings**。该修正后再次运行 test_chat_ownership.py，12 passed；未宣称全仓库 Pyright 已通过。npx 未改项目依赖。
- 浏览器隔离入口新增第二账号、BFF 双用户访问场景，全量 **21/21** 通过：甲创建会话、切换乙重放甲标识 404/保留输入，乙发送新会话成功。原登录、门禁、撤销重放等场景也通过。启动器超时上限 720 秒；临时服务、数据库/schema 自动清理，不调用真实模型、不操作开发业务表。
- 下一课保护运行时间线与取消入口。本课未解决同用户同会话并发顺序或普通聊天非模型失败时的缓存恢复问题。
