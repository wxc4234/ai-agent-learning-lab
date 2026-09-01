# Agent UI 事件设计

| 后端事件 | 前端状态 | UI 行为 |
|---|---|---|
| `RUN_STARTED` | `thinking` | 禁用发送按钮，显示“正在思考” |
| `TEXT_MESSAGE_START` | `streaming` | 创建空的助手消息气泡 |
| `TEXT_MESSAGE_CONTENT` | `streaming` | 按到达顺序追加文本片段 |
| `TEXT_MESSAGE_END` | `streaming` | 结束本条消息的流式显示 |
| `RUN_FINISHED` | `done` | 恢复输入框，记录本次运行完成 |
| `RUN_ERROR` | `error` | 显示可读错误和重试按钮 |