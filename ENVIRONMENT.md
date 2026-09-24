# 环境与依赖

本文件只维护当前安装、配置、启动、迁移、隔离测试与常见故障；不追加逐课验收记录。主场景为 `APP_MODE=local` 的 PC 工作台，账号模式保留为扩展。产品边界见 [本地模式说明](docs/local-runtime.md)。

截至 2026-09-23 的旧验收记录已完整搬到 [冻结历史证据](docs/history/verification-through-2026-09-23.md)，只在需要具体结果时按章节查阅；历史命令不代表当前默认回归范围。

## 1. 依赖与配置来源

| 依赖 | 当前约定 | 用途/事实来源 |
|---|---|---|
| Python | 3.12，每台电脑独立 `.venv` | `.python-version`；旧 3.10 环境不再适用 |
| Python 包 | 按锁定清单安装 | `requirements.txt`；FastAPI/Pydantic、SQLAlchemy/psycopg、Alembic、模型 SDK、pytest/httpx、Ruff |
| Node.js | 沿用项目 Node.js 24 环境 | Next.js/React 前端与 Node 测试 |
| pnpm | 10.34.1 | `apps/web/package.json` 的 packageManager；`pnpm-lock.yaml` 锁定依赖 |
| Docker Desktop/Compose | 本机可用 | `infra/compose.yaml` 提供 PostgreSQL + pgvector、Redis；Docker Sandbox 另按当前平台边界验收 |
| Git、模型 API Key | 本机配置 | 代码同步、模型请求；真实 Key 不进代码或文档 |

`.venv`、`node_modules`、数据库卷与真实密钥不跨电脑复制。Git 同步源码、依赖清单与迁移，不自动同步两台电脑的业务数据。

## 2. 首次安装

命令从仓库根目录执行，除非明确要求 `cd`。已有 `.env` 不覆盖；安装依赖失败时先解决错误再继续。

macOS / Linux（先安装 Git、Python 3.12、Node.js 与 Docker）：

```bash
git clone https://github.com/wxc4234/ai-agent-learning-lab.git
cd ai-agent-learning-lab
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
npm install --global pnpm@10.34.1
pnpm install --dir apps/web --frozen-lockfile
cp .env.example .env
```

Windows PowerShell（先安装 Git、Python 3.12、Node.js、Docker Desktop/WSL 2，重新打开终端）：

```powershell
git clone https://github.com/wxc4234/ai-agent-learning-lab.git
cd ai-agent-learning-lab
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm install --global pnpm@10.34.1
pnpm install --dir apps/web --frozen-lockfile
Copy-Item .env.example .env
```

已安装 uv 时可用 `uv pip install -r requirements.txt`。缺 pip 可先用项目解释器执行 `-m ensurepip --upgrade`。PowerShell 可直接调用 `.venv\Scripts\python.exe`；需要激活时使用 `.\.venv\Scripts\Activate.ps1`，受限终端仅按需设置当前进程的 ExecutionPolicy。

在根目录 `.env` 填写自己的 `DEEPSEEK_API_KEY`，按 `.env.example` 配置 `DATABASE_URL`、Redis 与模型设置。不要把服务端变量加上 `NEXT_PUBLIC_` 前缀。VS Code 的 Python 解释器选择项目 `.venv/bin/python`（Windows 为 `.venv\Scripts\python.exe`）。

## 3. 数据库服务与迁移

启动 Docker Desktop 后，从根目录运行：

```bash
docker compose -f infra/compose.yaml up -d
docker compose -f infra/compose.yaml ps
```

PostgreSQL 与 Redis 应为 healthy，仅回环访问，默认端口为 5432/6379，配置以 Compose 和本机 `.env` 为准。停止用 `docker compose -f infra/compose.yaml down`；`down -v` 会删除数据卷，不用于日常停机。

首次启动或同步新增迁移后，停止 API，在 `apps/api` 检查并显式升级。macOS/Linux：

```bash
cd apps/api
../../.venv/bin/python -m alembic current
../../.venv/bin/python -m alembic heads
../../.venv/bin/python -m alembic upgrade head
../../.venv/bin/python -m alembic current
../../.venv/bin/python -m alembic check
cd ../..
```

Windows 将上述解释器替换为 `..\..\.venv\Scripts\python.exe`。迁移前确认连接目标是本机预期数据库；重要数据先备份。预期 current 与源码 heads 一致，check 无新增结构操作；不要在文档里永久写死某课的 migration head。

API 启动只核对版本，不自动建表/升级。已有表但迁移缺失时停止排查，不能盲目 `stamp head`；版本一致也不保证没有手工结构漂移。同步代码前先核对 `git status --short --branch`，保留未提交学习改动，不用强制 reset。

## 4. 启动与停止 PC 工作台

完成依赖、模型配置、基础服务及迁移后，在根目录运行：

```bash
.venv/bin/python scripts/run_local.py
```

Windows：

```powershell
.\.venv\Scripts\python.exe scripts\run_local.py
```

打开 `http://127.0.0.1:3000`。启动器调用 `setup_local.py`，生成/复用本机随机内部凭证，同步根 `.env` 与 `apps/web/.env.local`，然后启动回环 API 8000 和 Web 3000；不会安装依赖或执行迁移。Ctrl+C 停止两个服务，Compose 服务另行停止。

浏览器只访问 Next 同源 BFF，内部凭证和模型 Key 留在服务端。本地模式的 API `/docs` 也受凭证保护，不能沿用旧账号模式裸访问的说明。修改环境变量或依赖后重启启动器；API 源码支持 reload。

需要分终端调试时，先在根目录运行 `.venv/bin/python scripts/setup_local.py`；随后在 `apps/api` 执行 `../../.venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1`，另一终端在 `apps/web` 执行 `pnpm dev`。Windows 使用对应 `.exe` 路径。两端必须使用一致的模式/凭证；`run_local.py` 会设置 local，不用于启动账号模式。

当前是源码本地版，安装包/自动依赖安装未交付。模型使用云 API 时，选入上下文的片段会发送至用户配置的模型服务。

Git状态采集样例目前仅支持POSIX，固定可执行文件 `/usr/bin/git`，不继承宿主Git配置或PATH。专项从根目录执行 `.venv/bin/python -m pytest apps/api/tests/workspace/git/test_status_capture.py -q`；只在临时目录初始化/准备Git历史并自动清理，不操作项目仓库的暂存、提交或推送，不需要数据库。

## 5. 定向测试与静态检查

选测时机、授权和范围遵循 [AGENTS.md](AGENTS.md#回归测试范围规则长期有效)。以下为选定单项后的命令示例，不是每课必跑清单；不要默认使用全量 pytest、整个领域脚本或全量 lint。

后端：在 `apps/api` 运行选定测试；可用 `-k` 或 `::用例名` 缩小到直接受影响用例。例如样例状态服务：

```bash
../../.venv/bin/python -m pytest -xq tests/workspace/samples/test_task_sample_status.py
../../.venv/bin/python -m ruff check app/services/workspace/samples/task_sample_binding.py
```

数据库测试复用 [conftest.py](apps/api/tests/conftest.py)：

- 只用 PostgreSQL + psycopg，不用 SQLite，不连接开发业务表。默认借用 DATABASE_URL 的服务器/账号信息，连接 `postgres` 维护库，每轮创建随机 `agent_lab_test_<uuid>` 数据库，每例独立 schema，允许真实 commit/rollback。
- 测试账号需要维护库连接和 CREATEDB 权限。可用 `TEST_DATABASE_ADMIN_URL` 指定测试服务器的 `postgresql+psycopg` 管理连接；不提交含密码的 URL，也不改变应用 DATABASE_URL。
- 测试正常结束或失败时清理本轮 schema/数据库；权限不足会失败，不静默跳过。强制杀进程可能残留，先核实确属本轮资源再清理，不批量删库。纯逻辑测试未请求 fixture 时不创建数据库。
- 迁移专项按需使用同一隔离配置，不执行开发数据清空或有损 downgrade 来“修测试”。

前端：在 `apps/web` 运行选定 Node 测试与定向 lint，例如样例状态 BFF：

```bash
node --experimental-strip-types --test test/features/workspaces/task-sample-status-route.test.ts
pnpm exec eslint src/app/api/_shared/task-sample-status-proxy.ts
```

工作台布局交互验收：仓库根目录运行 `BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=workbench-layout-wait.mjs .venv/bin/python apps/web/test/browser/run-isolated.py`，待服务就绪后浏览器访问 `http://localhost:13000/`。`apps/web/output/playwright/layout/fixture.json` 给出两组临时项目/任务 ID（含提案、空列表），通过侧栏选任务；输入 `[layout]` 触发固定用量回答；输入 `[typewriter]` 经真实流式适配器读取延迟 SDK chunk，用于检查逐片段 Markdown、停止与历史恢复。此夹具只准备显示状态，不执行真实文件应用。检查完在仓库根运行 `touch apps/web/output/playwright/layout/done`，启动器退出并清理临时服务与隔离数据库；650 秒未结束则超时失败。不连接开发业务数据。

TypeScript/路由契约变化时按影响执行 `pnpm typecheck`；构建只在构建链受影响或交付验收需要时运行 `pnpm build`。开发用 Node 需支持项目现用的 strip-types 参数。

PC 浏览器验收按任务选择现有隔离启动器或组件专项。通用工作台示例，从根目录运行：

```bash
BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=sample-status-workbench.mjs .venv/bin/python apps/web/test/browser/run-isolated.py
```

Windows 在 PowerShell 分别设置 `$env:BROWSER_APP_MODE`、`$env:BROWSER_TEST_SCRIPT` 后，用 `.\.venv\Scripts\python.exe` 运行同一脚本。浏览器入口按自身夹具要求配置 Playwright/Chrome；`PLAYWRIGHT_MODULE`、`CHROME_EXECUTABLE` 是否支持及其默认值先查对应入口，不把某台电脑的绝对路径写成通用要求。

补丁预览 PC 专项复用同一隔离启动器：

```bash
BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=patch-preview-tools.mjs .venv/bin/python apps/web/test/browser/run-isolated.py
```

同样支持 `PLAYWRIGHT_MODULE`/`CHROME_EXECUTABLE`。模型响应受控，授权读取与预览真实执行，浏览器结束后独立核对运行与零提案；自建临时文件和隔离数据库自动收尾。报告 `evidence.json`、`database-evidence.json` 及截图保存在 `/private/tmp/agent-ui-patch-preview/output/playwright/`，同名产物后次覆盖；当前入口按 macOS 环境验收。

Git 状态 PC 专项：同一隔离启动器使用 `BROWSER_APP_MODE=local BROWSER_TEST_SCRIPT=git-status.mjs`，支持 `PLAYWRIGHT_MODULE` / `CHROME_EXECUTABLE`。夹具在应用生命周期内创建Task与自有Git样例，不开放HTTP登记接口；受控模型须提供ModelUsage以通过既有预算检查。浏览器、独立数据库核对、文件与关闭清理报告写入 `apps/web/output/playwright/git-status/`，同名覆盖；失败时须同时检查服务日志与本轮报告，不能把旧报告当成功证据。

补丁提案 PC 专项：沿用上方命令，将 `BROWSER_TEST_SCRIPT` 改为 `patch-proposal-tools.mjs`。模型受控，保存/授权详情真实执行；专属夹具对 `unconfirmed.txt` 在真实保存后注入确认丢失，启动器独立核对数据库。报告及截图在 `/private/tmp/agent-ui-patch-proposal/output/playwright/`，同名覆盖；临时服务、样例文件和测试库自动清理。

补丁样例应用 PC 联合专项：同一启动器使用 `BROWSER_TEST_SCRIPT=patch-application.mjs`。可信夹具创建四个受限样例；生产工作台保存/审批，临时 `/sample-apply` 页面挂载既有应用组件，历史详情重新查询状态。报告、截图及清理核对在 `/private/tmp/agent-ui-patch-application/output/playwright/`；路径控制文件仅供本地夹具使用。封锁样例的清理由测试进程在HTTP停止并核对非活动状态后完成，不是产品恢复入口。



验收需要说明 PC 视口、真实请求链、模型/上游模拟边界、文件/数据库证据与资源清理；测试报告和截图沿用对应脚本的 output 目录。Docker 实机专项按当前平台能力单独选择，普通单测不能代替真实隔离证据。

Task 样例命令 PC→真实 Docker 专项（当前 macOS Docker Desktop），从根目录运行：

```bash
.venv/bin/python apps/web/test/browser/run-task-sample-command.py
```

先通过 `PLAYWRIGHT_MODULE` 指定可用 Playwright 模块（已可解析时可省略），通过 `CHROME_EXECUTABLE` 指定本机可运行的 Chromium；要求上述 PostgreSQL 权限及本机 Docker/批准镜像。入口复用隔离启动器，模型/标题和取消通知受控，真实执行 Task 快照与命令，删除回执丢失只在夹具注入。每轮独立就绪标记，退出核对 journal 和原文件，显式清理自有资源；异常退出不能据此假定清理成功。截图、`browser-evidence.json` 与 `server-evidence.json` 写入 `tempfile.gettempdir()/agent-task-sample-browser/output/playwright/`，同名文件被下一次验收覆盖，不把这些临时文件当持久恢复存储。


样例只读挂载实机专项（当前仅 macOS Docker Desktop），从仓库根目录运行：

```bash
PYTHONPATH=apps/api .venv/bin/python scripts/verify_sandbox_sample.py
```

该脚本使用现有批准镜像和固定本机 socket；需允许访问 Docker socket，不自动拉镜像或启动 Docker。它只创建随机标识的自有容器/样例，确认容器缺失后清理来源；失败时保留输出中的 token、完整 ID 和源目录供核对，不能批量删除其他容器或将连接失败当作对象不存在。样例中途创建/部分清理失败也可能保留现场；不得递归盲删。本地聊天已通过 Task 绑定与独立快照接入此挂载入口，普通项目目录仍不开放。

内部样例命令的完整 attach 编排专项，同样从根目录运行：

```bash
PYTHONPATH=apps/api .venv/bin/python scripts/verify_sandbox_sample_command.py
```

它执行固定读取、非零退出、大输出、超时/取消与响应丢失注入场景；外部 Docker 操作真实执行，超时预算和故障点只在脚本作用域中调整。正常由服务清理，异常先验证来源仍被保留，再由脚本重核身份并显式收尾。脚本输出每个场景的 token、完整 ID、源路径与清理事实；不要将该测试收尾路径视为生产自动恢复服务。

失败样例命令的只读诊断专项，从根目录运行：

```bash
PYTHONPATH=apps/api .venv/bin/python scripts/verify_sandbox_sample_reconciliation.py
```

脚本先构造自有失败现场，再单独观察生命周期/来源变化；观察区间只允许 `container ls/inspect`，比较文件、登记和容器配置前后不变。脚本在区间外显式创建/启动/停止/删除自有目标，包括一次同名替换；诊断函数本身无清理权。异常中断仍须根据输出的 token、ID 和源位置核对现场。

失败样例命令的显式清理专项，从根目录运行：

```bash
PYTHONPATH=apps/api .venv/bin/python scripts/verify_sandbox_sample_cleanup.py
```

仅处理本轮拥有者保存的已知完整 ID 与已登记来源。运行中拒绝由脚本单独停止；删除回执丢失/取消先保留来源，再明确调用清理重新确认缺失。脚本输出完整 ID 和释放事实，异常保留定位；未知 ID、部分文件现场不得盲目重试或递归删除。

Task 快照到命令执行的真实 PostgreSQL + Docker 专项，从 `apps/api` 运行：

```bash
../../.venv/bin/python -m pytest ../../scripts/verify_task_sample_command.py -q -s
```

脚本复用根测试夹具的独立数据库/私有 schema 和服务端 Task 绑定，真实执行容器；输出每个场景的完整 ID 与清理事实。创建回执丢失场景由脚本独立审计 ID 后显式收尾，产品不会自动接管未知 ID。仅操作本轮自有容器、快照和 Task 样例，不访问开发业务表。

## 6. 常见问题

| 现象 | 检查与处理 |
|---|---|
| 无法导入 fastapi/uvicorn，或 VS Code 标红 | 用项目解释器检查 `-c "import sys; print(sys.executable)"` 与 `-m pip show fastapi`；重选 `.venv`，必要时重载编辑器 |
| Python 版本过旧 | 检查项目 `.venv`，系统 Python 升级不会更新已有虚拟环境；按 3.12 重建本机环境 |
| Windows 找不到 pnpm | 重开终端，检查 Node/PATH，按 packageManager 安装 pnpm |
| 数据库连接失败 | 检查 Docker Engine、Compose healthy、本机 DATABASE_URL 与端口；不要改用 SQLite 绕过 |
| API 拒绝启动/提示迁移不一致 | 在正确数据库核对 current/heads，显式升级并 check；不要直接 stamp |
| 本地请求 403 | 检查两端 APP_MODE、内部凭证、回环地址与 Origin；重新同步配置并重启，不向浏览器暴露凭证 |
| 模型调用失败 | 看服务端脱敏错误，核对 API Key、模型配置、额度与网络；浏览器报错不等于数据库未提交 |
| 修改后未生效 | 源码检查 reload，配置/依赖重启启动器，确认操作的是当前 main 目录 |
| 旧练习 input 等待 | 在终端输入并回车；第 1 周练习按该周 README 运行，不混入当前 API 启动链 |

`.env`、`.env.local`、`.venv`、node_modules 和数据库文件/卷不进入 Git；配置示例只放占位符。新增环境故障仅记录可复用原因与解决步骤，不追加某次验收的完整流水。
