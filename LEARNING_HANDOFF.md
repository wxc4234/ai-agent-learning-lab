# AI Agent 学习交接

更新时间：2026-08-28（Asia/Shanghai）

这份文件用于在 macOS、Windows 和新的 AI 对话之间持续交接学习状态。每次完成一个阶段，只需要更新“当前断点”“已完成内容”和“下一步任务”，不再依赖某一段聊天记录。

## 1. 转型目标与学习规则

目标：在不超过 **3 个月**的时间内，从前端开发转向能够独立开发、调试和部署 AI Agent 应用的工程师。

学习规则：

- 优先学习求职和作品集真正会用到的内容。
- 每个新概念先讲“是什么 → 为什么需要 → 怎么使用 → 示例”。
- 一次只推进一个可以运行、可以验证的小任务。
- 30% 学习概念，70% 动手写代码。
- 不长期停留在普通 CRUD，始终围绕 LLM、RAG、Tool Calling 和 Agent 工程能力推进。

## 2. 当前一句话进度

已经完成一个可运行的 FastAPI + DeepSeek 对话后端，支持 `session_id` 多会话隔离、最近 5 轮上下文、SQLite 持久化，以及第一个完整的 Tool Calling 循环。

当前准确断点：**无参数时间工具已经完成；下一课开始实现带参数工具和通用工具调度。**

## 3. 仓库与主要文件

- GitHub：<https://github.com/wxc4234/ai-agent-learning-lab>
- 当前学习目录：`week-01/fastapi_app`
- 主接口：`chat_api.py`
- SQLite：`database.py`
- Agent 工具：`agent_tools.py`
- 普通 FastAPI 练习：`main.py`
- 环境说明：`week-01/环境与依赖.md`
- 第 1 周进度：`week-01/README.md`

主要文件职责：

```text
chat_api.py
  ├── FastAPI 路由
  ├── DeepSeek 调用
  ├── 会话记忆与滑动窗口
  └── Tool Calling 测试流程

database.py
  ├── 创建 messages 表
  ├── 保存 user/assistant 一轮消息
  └── 按 session_id 读取历史

agent_tools.py
  ├── get_current_time() 真实函数
  ├── TOOLS 模型工具说明
  └── TOOL_FUNCTIONS 安全执行白名单
```

## 4. 回到 Windows 后恢复项目

不要复制 macOS 的 `.venv`。Windows 必须重新创建虚拟环境。

```powershell
git clone https://github.com/wxc4234/ai-agent-learning-lab.git
cd ai-agent-learning-lab

py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

打开 `.env` 并填写：

```dotenv
DEEPSEEK_API_KEY=你的真实key
```

选择 VS Code 解释器：

```text
.venv\Scripts\python.exe
```

启动项目：

```powershell
cd week-01\fastapi_app
python -m uvicorn chat_api:app --reload
```

打开 Swagger：<http://127.0.0.1:8000/docs>。

如果仓库已经存在，不要重新克隆，进入仓库后执行：

```powershell
git pull origin main
```

## 5. 当前接口

| 接口 | 作用 | 验证方式 |
|---|---|---|
| `GET /` | 检查服务是否启动 | 浏览器直接打开 |
| `GET /chat` | 提示聊天需要 POST | 浏览器直接打开 |
| `POST /chat` | DeepSeek 多轮对话 | 传入 `session_id` 和 `prompt` |
| `GET /sessions/{session_id}/messages` | 查询 SQLite 历史 | 使用已有的 session_id |
| `POST /tool-test` | 完整时间工具调用 | 提问“现在几点了？” |

`POST /chat` 示例：

```json
{
  "session_id": "test-001",
  "prompt": "你好，请介绍一下你自己"
}
```

`POST /tool-test` 示例：

```json
{
  "prompt": "现在几点了？请用中文回答"
}
```

已经验证的工具调用结果结构：

```json
{
  "type": "final_answer",
  "tool_name": "get_current_time",
  "tool_result": "2026-08-28 17:24:10",
  "reply": "现在是 2026 年 8 月 28 日下午 5 点 24 分。"
}
```

## 6. 已完成知识点

### Python 与 FastAPI

- 函数、循环、列表、字典、异常处理、JSON、类和对象。
- FastAPI 应用、GET/POST/PUT、路径参数、查询参数和 Swagger。
- Pydantic `BaseModel` 请求校验和响应模型。
- `HTTPException` 和常见的 404、405、422、500、502。
- `async`/`await` 等待模型网络请求。

### LLM 对话后端

- 使用 `AsyncOpenAI` 兼容接口调用 DeepSeek。
- 使用 `.env` 和 `load_dotenv()` 读取 `DEEPSEEK_API_KEY`。
- 理解模型接口本身无状态，应用必须重新发送历史消息。
- 使用 `session_id` 隔离多个会话。
- 始终保留 system、最近 5 轮历史和当前问题。
- 清理内存中的旧消息，避免上下文不断增长。

### SQLite 持久化

- 使用 `sqlite3.connect()` 连接本地数据库。
- 使用 `CREATE TABLE IF NOT EXISTS` 创建 `messages` 表。
- 使用参数化 SQL 保存 user 和 assistant 消息。
- 使用 `SELECT ... WHERE session_id = ? ORDER BY id ASC` 恢复历史。
- 已验证 FastAPI 重启后，同一个 `session_id` 仍能恢复记忆。
- 已增加历史记录查询接口。

### Tool Calling

- 理解模型只负责选择工具和生成参数，Python 才真正执行工具。
- 使用 JSON Schema 向模型描述工具。
- 使用 `message.tool_calls` 获取模型的工具请求。
- 使用 `tool_call.type` 完成 Pylance 类型缩小。
- 使用 `TOOL_FUNCTIONS` 白名单，避免执行任意函数。
- 使用 `role="tool"` 和 `tool_call_id` 把执行结果交回模型。
- 已完成“用户提问 → 模型选工具 → Python 执行 → 模型最终回答”的完整闭环。

## 7. 当前代码运行机制

### 带数据库记忆的聊天

```text
POST /chat
  → 根据 session_id 找到内存会话
  → 内存中不存在时，从 SQLite 恢复历史
  → 追加当前 user 消息
  → 选择最近 5 轮上下文
  → 调用 DeepSeek
  → 保存 user/assistant 到 SQLite
  → 追加 assistant 消息并清理旧内存
  → 返回 reply
```

### 当前工具调用

```text
用户询问时间
  → 第一次调用 DeepSeek
  → 模型返回 get_current_time 工具请求
  → Python 从白名单找到并执行函数
  → 将 tool 结果加入 messages
  → 第二次调用 DeepSeek
  → 模型生成自然语言最终回答
```

## 8. 下一课：带参数工具

当前不要重复实现时间工具，也不要直接跳到框架。按照下面顺序继续：

1. 在 `agent_tools.py` 新增 `add_numbers(a, b)`。
2. 在 `TOOLS` 中定义 `a`、`b` 的 JSON Schema，类型使用 `number`，并设为必填。
3. 观察模型生成的 `tool_call.function.arguments`。它是 JSON 字符串，不是 Python 字典。
4. 使用 `json.loads()` 把参数字符串转换成字典。
5. 使用 `tool_function(**arguments)` 将字典参数传给函数。
6. 对未知工具、非法 JSON、缺少参数和工具异常分别返回清晰错误。
7. 验证“计算 25 加 37”能够调用工具并回答 62。

这一步的验收流程：

```text
用户：计算 25 加 37
模型：add_numbers({"a": 25, "b": 37})
Python：62
模型：25 加 37 等于 62
```

## 9. 后续任务顺序

### A. 完善 Agent 工具循环

- [ ] 支持带参数工具。
- [ ] 抽取通用 `execute_tool()`，减少接口中的重复判断。
- [ ] 支持多个工具，并根据名称安全分发。
- [ ] 支持模型连续调用多个工具，设置最大循环次数防止死循环。
- [ ] 把 Tool Calling 接入带 `session_id` 记忆的 `/chat`。
- [ ] 确定工具消息如何持久化，避免重启后上下文损坏。

### B. 工程质量

- [ ] 统一处理 DeepSeek、SQLite、JSON 和工具执行异常。
- [ ] 增加日志：session、工具名称、耗时、成功或失败。
- [ ] 用 pytest 测试数据库和工具函数。
- [ ] 拆分路由、模型客户端、数据库和工具模块。
- [ ] 增加流式输出，为后续聊天前端做准备。
- [ ] 补充接口说明和运行截图，形成作品集材料。

### C. RAG

- [ ] 学习文档加载和文本切分。
- [ ] 学习 Embedding 和向量相似度。
- [ ] 使用向量数据库保存和检索片段。
- [ ] 完成本地文档问答接口。
- [ ] 返回答案引用来源。
- [ ] 编写基础检索和回答评测集。

### D. 求职项目

- [ ] 将 RAG、SQLite、Tool Calling 组合成一个完整 Agent 项目。
- [ ] 增加可观测性、测试、部署和安全说明。
- [ ] 编写架构图、README、演示视频和项目复盘。
- [ ] 把项目经历转换成简历描述和面试讲解。

## 10. 3 个月路线

| 时间 | 主线 | 必须产出 |
|---|---|---|
| 第 1～2 周 | Python、FastAPI、LLM、SQLite、Tool Calling | 可运行的多轮对话与多工具 Agent API |
| 第 3～4 周 | RAG、向量检索、引用和评测 | 带来源的文档问答服务 |
| 第 5～8 周 | 工作流、状态管理、多工具、测试、日志 | 完整的业务型 Agent 项目 |
| 第 9～10 周 | 部署、性能、安全和演示 | 可在线访问的作品集项目 |
| 第 11～12 周 | 简历、项目表达和面试准备 | 简历项目、面试题与投递材料 |

## 11. 已解决的典型问题

- 终端使用 Python 2.7：创建并选择项目 `.venv` 中的 Python 3.10。
- VS Code 无法解析 `fastapi`：重新选择正确解释器。
- `input()` 让 IDE 像卡住：输入正在等待终端交互；FastAPI 使用 HTTP 请求传参。
- `GET /chat` 返回 405：浏览器默认发送 GET，而真正聊天接口使用 POST。
- `500 Internal Server Error`：查看 Uvicorn 终端中的真实 Python 异常。
- SQLite `no such table`：建表和 `INSERT INTO` 的表名必须完全一致。
- Pylance 的工具属性报错：先判断 `tool_call.type == "function"` 完成类型缩小。
- Swagger 中出现 `**文字**`：这是模型返回的 Markdown 加粗语法，不是错误。

## 12. 安全与提交约定

- `.env`、`.venv/`、`*.db` 和 `__pycache__/` 不提交 GitHub。
- 不在代码、文档、截图或聊天中暴露真实 API Key。
- 不使用 `eval()` 执行模型生成的函数名或参数。
- 工具只能通过明确的白名单调用。
- 每完成一个可运行阶段再提交，提交前执行格式、语法和核心功能检查。

## 13. 新对话接续提示词

复制下面内容到新的 AI 对话：

```text
你是我的 AI Agent 应用开发导师。我是一名前端开发工程师，目标是在不超过 3 个月内转型为 AI Agent 应用开发工程师。

请先读取仓库根目录的 LEARNING_HANDOFF.md，并遵守其中的学习规则：每个新概念先按“是什么、为什么需要、怎么使用、示例”讲解；一次只给一个小任务，等我完成后再继续。

我已经完成 FastAPI + DeepSeek 多轮对话、session_id 会话隔离、最近 5 轮滑动窗口、SQLite 持久化，以及无参数 get_current_time 的完整 Tool Calling 循环。

请从“带参数工具”继续：先讲解工具 arguments 为什么是 JSON 字符串，再带我实现 add_numbers(a, b)、json.loads() 参数解析和安全工具执行。不要重复前面已经完成的内容，也不要一次输出很长。
```

## 14. 每次学习结束时如何维护本文件

只更新以下内容即可：

1. 修改顶部“更新时间”。
2. 更新“当前一句话进度”和“当前准确断点”。
3. 在“已完成知识点”中补充本次成果。
4. 勾选“后续任务顺序”中的完成项。
5. 把“下一课”改成下一次真正要开始的任务。
6. 更新最后的“新对话接续提示词”。
