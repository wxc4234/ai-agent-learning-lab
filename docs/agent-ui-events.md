# Agent UI 事件设计

| 后端事件 | 前端状态 | UI 行为 |
|---|---|---|
| `RUN_STARTED` | `thinking` | 禁用发送按钮，显示“正在思考” |
| `RUN_CANCELLATION_REQUESTED` | 保持当前状态 | 记录用户停止或超时原因，等待流实际终止 |
| `TOOL_CALL_START` | `thinking` | 新增工具卡片，显示工具名、参数和“运行中” |
| `TOOL_CALL_RESULT` | `thinking` | 将对应工具卡片更新为“成功”并显示结果 |
| `TOOL_CALL_ERROR` | `thinking` | 将对应工具卡片更新为“失败”；允许模型根据错误继续决策 |
| `TEXT_MESSAGE_START` | `streaming` | 创建空的助手消息气泡 |
| `TEXT_MESSAGE_CONTENT` | `streaming` | 按到达顺序追加文本片段 |
| `TEXT_MESSAGE_END` | `streaming` | 结束本条消息的流式显示 |
| `RUN_FINISHED` | `done` | 恢复输入框，记录本次运行完成 |
| 用户点击停止或请求被取消 | `aborted` | 保留已生成片段，标记本次运行已停止 |
| `RUN_ERROR` | `error` | 显示可读错误和重试按钮 |

## Day 5 状态约束

- 新请求从 `idle`、`done`、`aborted` 或 `error` 进入 `thinking`。
- 工具事件不会直接改变整次运行的终态；只有 `TEXT_MESSAGE_START` 到达后才从 `thinking` 进入 `streaming`。
- 正常结束只能进入 `done`；用户取消只能进入 `aborted`；网络、超时、500、502 或限流只能进入 `error`。
- `error` 重试会清空半截回答，但复用原问题和会话标识，避免在 UI 中追加重复回答。

## 传输协议

`POST /chat/stream` 使用 `application/x-ndjson`：每个事件是一行独立 JSON。前端必须先按换行重组网络分块，再解析事件，不能假设一次 `reader.read()` 恰好得到一条完整事件。

## 错误提示

前端不直接展示后端原始异常：429 显示“请求太频繁了”，502 显示“模型服务暂时不可用”，其他 5xx 显示“服务暂时出错”，网络断开和超时分别给出检查网络、稍后重试的提示。
