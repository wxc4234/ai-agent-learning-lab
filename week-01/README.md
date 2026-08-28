# 第 1 周：Python、FastAPI 与首个 Agent 循环

周期：2026-08-24 ～ 2026-08-30
目标：用 Python 和 FastAPI 完成可持久化的 LLM 对话接口，并理解最小 Tool Calling 流程。

## 本周已经完成

- [x] Python 函数、列表、字典、异常、JSON 和类
- [x] FastAPI 的 GET、POST、PUT、路径参数、查询参数与 Swagger
- [x] 使用 Pydantic 校验请求体和响应体
- [x] 使用 `AsyncOpenAI` 调用 DeepSeek
- [x] 使用 `.env` 管理 `DEEPSEEK_API_KEY`
- [x] 使用 `session_id` 隔离不同对话
- [x] 最近 5 轮滑动窗口和内存清理
- [x] 使用 SQLite 保存并恢复历史消息
- [x] 提供历史记录查询接口
- [x] 完成无参数工具 `get_current_time`
- [x] 完成最小 Agent 循环：选择工具 → 执行工具 → 返回结果 → 生成最终回答

## 当前可运行接口

| 接口 | 作用 |
|---|---|
| `GET /` | 检查服务是否启动 |
| `GET /chat` | 提示聊天接口需要使用 POST |
| `POST /chat` | 带 `session_id`、SQLite 记忆的 DeepSeek 对话 |
| `GET /sessions/{session_id}/messages` | 查询数据库中的会话历史 |
| `POST /tool-test` | 测试模型调用本地时间工具 |

启动方式和 Windows/macOS 环境配置见：[环境与依赖](环境与依赖.md)。

## 回到 Windows 后先做什么

1. 克隆或更新仓库，重新创建 Windows 虚拟环境。
2. 创建本机 `.env`，填入 `DEEPSEEK_API_KEY`。
3. 启动 `chat_api.py`，打开 `/docs`。
4. 验证同一 `session_id` 在服务重启后仍能恢复记忆。
5. 调用 `/tool-test`，确认能返回 `get_current_time` 的结果和模型最终回答。

## 下一课：带参数工具

不要继续扩展普通 CRUD。下一课从这里开始：

1. 新增一个带 `a`、`b` 参数的计算工具。
2. 在工具 JSON Schema 中声明参数类型和必填项。
3. 使用 `json.loads()` 解析模型返回的 `arguments`。
4. 使用工具白名单执行 `tool_function(**arguments)`。
5. 给错误参数、未知工具和工具执行失败增加明确异常处理。

验收示例：

```text
用户：计算 25 加 37
模型：调用 add_numbers({"a": 25, "b": 37})
Python：返回 62
模型：最终回答“25 加 37 等于 62”
```

## 后续任务顺序

### 第 1 阶段：完善 Tool Calling

- [ ] 支持带参数工具
- [ ] 抽取通用工具调度函数，避免在接口里写大量判断
- [ ] 支持模型连续调用多个工具，并设置最大循环次数
- [ ] 把工具调用接入带记忆的 `/chat`，不再只使用 `/tool-test`
- [ ] 保存必要的工具调用记录，避免破坏对话上下文

### 第 2 阶段：工程质量

- [ ] 统一处理 DeepSeek、SQLite、JSON 参数和工具执行异常
- [ ] 增加日志，记录 session、工具名称、耗时和执行结果
- [ ] 使用 pytest 为数据库和工具函数编写测试
- [ ] 把路由、数据库、模型客户端和工具拆分成清晰模块
- [ ] 增加流式输出，让前端逐字接收模型回答

### 第 3 阶段：进入 RAG

- [ ] 理解文档加载、切分、Embedding 和向量检索
- [ ] 完成一个本地文档问答接口
- [ ] 返回答案时附带检索来源
- [ ] 评估召回结果和回答准确性

## 3 个月主线

```text
第 1～2 周：Python、FastAPI、LLM、SQLite、Tool Calling
第 3～4 周：RAG、向量检索、引用与评测
第 5～8 周：多工具 Agent、工作流、状态管理、测试与可观测性
第 9～12 周：求职作品集、部署、项目文档、简历与面试准备
```

学习规则保持不变：每个新概念先学习“是什么 → 为什么需要 → 怎么使用 → 示例”，一次只推进一个可运行、可验证的小任务。
