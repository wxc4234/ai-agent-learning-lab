# 环境与依赖

这份文档专门说明：**需要安装什么、如何选择 Python 解释器、怎样启动项目，以及出错时如何排查**。

## 1. 项目需要什么

- Git
- Python 3.10 或更高版本
- VS Code（推荐）
- Docker Desktop（启动 PostgreSQL + pgvector 与 Redis）
- DeepSeek API Key（调用 `/chat` 时需要）

当前 `apps/api` 是 Python 项目，运行它**不需要 pnpm 或 Node.js**。第 3 周开始开发 `apps/web` 中的 Next.js Agent 前端时才会使用 Node.js 和 pnpm，届时会补充前端环境说明。

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
```

### Windows PowerShell

```powershell
git clone https://github.com/wxc4234/ai-agent-learning-lab.git
cd ai-agent-learning-lab

py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install -r requirements.txt
Copy-Item .env.example .env
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

## 3. 配置 DeepSeek API Key

在项目根目录找到刚复制出来的 `.env`，填写：

```dotenv
DEEPSEEK_API_KEY=在这里填写你的key
```

注意：

- 不要把真实 Key 写进 Python 代码或文档。
- `.env` 已被 `.gitignore` 忽略，不会提交到 GitHub。
- `chat_api.py` 已调用 `load_dotenv()`，会主动读取 `.env`。
- VS Code 提示 `python.terminal.useEnvFile` 时，可以启用它，但项目读取 Key 并不依赖这个设置。

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

它们已经写在 `.gitignore` 中。`.env.example` 可以提交，但里面只能保留占位符，不能放真实 Key。
