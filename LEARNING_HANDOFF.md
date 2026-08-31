# AI Agent 学习交接

更新时间：2026-08-31（Asia/Shanghai）

这份文件只记录**当前进度、恢复方式和下一课**。完整路线统一查看 [LEARNING_PLAN.md](LEARNING_PLAN.md)，逐日任务与验收标准查看 [LEARNING_CURRICULUM.md](LEARNING_CURRICULUM.md)。

目标岗位：**AI Agent 应用开发工程师（偏前端 / 全栈产品工程）**。当前学习后端是为了能够独立交付 Agent 产品，不是转向纯后端或泛化的大模型应用岗位。

## 目录规则（长期有效）

`week-XX/` 只保存单周记录和一次性练习。任何会被后续周次继续开发的源码、配置、测试、环境说明或基础设施，都不得放入某个 `week-XX/` 目录。当前跨周 FastAPI 后端统一位于 `apps/api/`，后续 Next.js 前端放入 `apps/web/`。详细规则见 [AGENTS.md](AGENTS.md)。

## 当前进度

第 1 周已经完成，当前进入第 2 周：**为后续 Next.js Agent 前端准备结构清晰、可测试、可连接 PostgreSQL 的 Agent 后端。**

当前代码已经具备：

- FastAPI + DeepSeek 对话接口。
- `session_id` 会话隔离和最近 5 轮上下文。
- SQLite 对话持久化。
- 无参数时间工具 `get_current_time`。
- 一次完整的 Tool Calling 执行闭环。

尚未完成：带参数工具、通用工具调度、多工具循环。这些内容不会删除，统一放到第 7 周 Agent Runtime 阶段完成。

## 当前文件

| 文件 | 作用 |
|---|---|
| `apps/api/main.py` | FastAPI 应用入口，注册路由并初始化数据库 |
| `apps/api/database.py` | SQLite 初始化、保存和读取 |
| `apps/api/agent_tools.py` | 工具描述、时间函数和执行白名单 |
| `ENVIRONMENT.md` | 安装、启动和常见问题 |

## 当前接口

| 接口 | 作用 |
|---|---|
| `GET /` | 服务健康检查 |
| `GET /chat` | 提示使用 POST |
| `POST /chat` | 带 SQLite 记忆的 DeepSeek 对话 |
| `GET /sessions/{session_id}/messages` | 查询会话历史 |
| `POST /tool-test` | 测试时间工具调用 |

## 下一课

第 2 周 Day 1：**项目分层与配置管理。**

本课先完成一个小目标：在 `apps/api/config.py` 建立统一配置对象，把“配置”从 `chat_api.py` 中移出去。学习顺序：

1. 配置是什么。
2. 为什么 API Key、模型名和数据库地址不应该散落在路由文件中。
3. 使用 Pydantic Settings 创建统一配置对象。
4. 让 `chat_api.py` 使用配置对象，并验证接口仍然可运行。

验收标准：

- 没有把真实 Key 写进代码。
- 缺少 `DEEPSEEK_API_KEY` 时能看到清楚的配置错误。
- 服务能启动，`POST /chat` 仍然正常返回。

完成这一步后，再继续拆分路由、模型客户端、Schema 和数据库层。不要一次重构全部文件。第 3 周会把这个 API 接入 Next.js，并开始实现 streaming 和 Agent 状态界面。

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
python -m uvicorn main:app --reload
```

打开 <http://127.0.0.1:8000/docs>。不要复制其他操作系统生成的 `.venv`。

## 每次学习结束只更新这里

后续只需要维护三处内容：

- 当前进度。
- 下一课和验收标准。
- 新增的重要文件或启动变化。

不要再把完整 12 周路线复制到本文件。
