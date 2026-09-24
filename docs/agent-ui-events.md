# Agent UI 事件设计

| 后端事件 | 前端状态 | UI 行为 |
|---|---|---|
| `RUN_STARTED` | `thinking` | 禁用发送按钮，显示“正在思考” |
| `RUN_CANCELLATION_REQUESTED` | 保持当前状态 | 记录用户停止或超时原因，等待流实际终止 |
| `TOOL_CALL_START` | `thinking` | 新增工具卡片，显示工具名、参数和“运行中” |
| `TOOL_CALL_RESULT` | `thinking` | 将对应工具卡片更新为“成功”，显示结果与单次耗时 |
| `TOOL_CALL_ERROR` | `thinking` | 将对应工具卡片更新为“失败”，显示执行耗时或“未进入执行阶段”；允许模型根据错误继续决策 |
| `TEXT_MESSAGE_START` | `streaming` | 创建空的助手消息气泡 |
| `TEXT_MESSAGE_CONTENT` | `streaming` | 按到达顺序追加文本片段 |
| `TEXT_MESSAGE_END` | `streaming` | 结束本条消息的流式显示 |
| `RUN_FINISHED` | `done` | 恢复输入框，保存并展示步骤数、Token、费用和耗时摘要 |
| 用户点击停止或请求被取消 | `aborted` | 保留已生成片段，标记本次运行已停止 |
| `RUN_ERROR` | `error` | 显示可读错误和重试按钮；预算耗尽与 usage 未知使用稳定错误码 |

## Day 5 状态约束

- 新请求从 `idle`、`done`、`aborted` 或 `error` 进入 `thinking`。
- 工具事件不会直接改变整次运行的终态；只有 `TEXT_MESSAGE_START` 到达后才从 `thinking` 进入 `streaming`。
- 正常结束只能进入 `done`；用户取消只能进入 `aborted`；网络、超时、500、502 或限流只能进入 `error`。
- `error` 重试会清空半截回答，但复用原问题和会话标识，避免在 UI 中追加重复回答。

## 传输协议

`POST /chat/stream` 使用 `application/x-ndjson`：每个事件是一行独立 JSON。前端必须先按换行重组网络分块，再解析事件，不能假设一次 `reader.read()` 恰好得到一条完整事件。

`TOOL_CALL_RESULT.duration_ms` 为单次成功执行的非负整数耗时。`TOOL_CALL_ERROR.duration_ms` 在执行异常或超时时为非负整数，在未知工具或参数校验失败时为 `null`；字段不会省略。浏览器解析器会按错误代码校验这一阶段语义，拒绝缺失字段、非法整数和错误的空值。聊天状态中的 `durationMs` 在工具运行时不存在，结束后原样保存为整数或 `null`。

`max_steps_exceeded`、`token_budget_exhausted`、`token_usage_unknown` 三种 Agent Loop 终态的 `RUN_ERROR` 会额外携带 `steps_taken` 与和成功事件同结构的 `metrics`，用于复核失败前已经产生的成本；其他模型、网络或服务错误仍可只有 `code` 与 `message`。

浏览器解析器要求 `steps_taken` 与 `metrics` 同时出现或同时缺失；完整失败摘要复用 `RUN_FINISHED` 的字段校验。完整失败摘要写入聊天状态，并可从持久化 Run 终态事件恢复。

## 模型文本增量与保存

`/chat/stream` 使用 `StreamingDeepSeekDecisionMaker` 发起 `stream=True` 请求。公开正文 delta 经 Runtime 的 `ModelTextDelta` 原样转为 NDJSON 文本事件，Markdown 随片段更新；不发送隐藏推理或未完成的工具参数。工具 id/name/arguments 按索引拼接，明确 `tool_calls` 终态且流完整结束后，才进入现有注册表、参数校验与预算门禁。一次仍只支持一个工具调用。

同一 Run 中不同模型步骤的公开文本用空行分隔。增量片段只用于实时传输；成功结束后把完整可见文本一次保存为助手消息，并记录一份完整 Run 文本，不逐片段写数据库。历史恢复直接显示完整消息，不重播打字机效果。取消、断流或截断时保留页面已显示片段并进入失败/取消态，不保存为完整会话回答、不执行不完整工具、不自动重试。

模型调用结束后的 usage 用于指标与续跑预算；缺失值保持未知。每步公开文本与工具字段合计最多 1 MiB，缺少明确终态的 EOF 不算成功。关闭浏览器消费或取消请求时，嵌套生成器显式关闭上游流。非流式 `/chat` 保留兼容入口。

## 流式阅读位置

发送或重试时，将本轮问题定位到对话滚动区顶部，并为本轮内容预留至少一屏可用高度。后续文本增量和终态不触发再次定位；关闭该滚动区的浏览器自动滚动锚定，用户上翻历史时保持阅读位置。窗口尺寸变化只调整预留高度，切换任务后的历史展示不重播发送定位。

## 错误提示

前端不直接展示后端原始异常：429 显示“请求太频繁了”，502 显示“模型服务暂时不可用”，其他 5xx 显示“服务暂时出错”，网络断开和超时分别给出检查网络、稍后重试的提示。

正式聊天的 Token 续跑预算由后端 `AGENT_MAX_TOTAL_TOKENS` 控制，浏览器不能覆盖。`token_budget_exhausted` 表示已知累计用量达到预算，`token_usage_unknown` 表示模型没有返回 usage，Runtime 为避免未知成本而停止继续执行。
