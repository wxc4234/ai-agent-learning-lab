# 本机模型设置

侧边栏底部“模型设置”只在 `APP_MODE=local` 显示。填写服务地址、模型 ID 和 API Key，支持兼容 OpenAI Chat Completions 的聊天服务；Agent 工具调用仍要求模型支持工具协议。聊天模型不需要向量维度，文档中的上下文/输出 Token 数不是向量维度。

代码检索模型是独立、可选的 Embedding 服务，不复用聊天模型凭证。“检测维度”向所填服务的 `/embeddings` 发送一段固定英文测试文字，不含项目内容；可能产生费用。检测不传 `dimensions`，从返回向量获得默认长度，严格检查响应后填入表单；须点击保存才生效。也可按供应商文档填写维度，只有服务明确支持时才开启“请求指定维度”。当前项目支持 1～4096 维，这不是所有模型的通用限制；改变模型或维度不会迁移旧向量，后续检索仍校验模型空间。

## 存储与生效

- 浏览器只通过同源 `/api/model-settings` BFF；API 校验本地运行凭证与写入 Origin。密钥输入框提交后清空，读取响应只有 `key_configured`，不返回密钥，也不写 localStorage。
- 保存覆盖写入根目录 `.local/model-settings.json`，已被 Git 忽略；POSIX 文件权限 0600、目录创建权限 0700。它是明文配置文件，不是加密密钥库；Windows 权限尚未验收。宿主可用 `MODEL_SETTINGS_PATH` 指定独立位置，浏览器不能选择路径。
- 未保存的通道沿用 `.env`；本机保存值优先，服务重启后仍保留。保存本身不调用模型、不生成索引，也不修改 `.env`。启用时必须有有效地址、模型和密钥，Embedding 还需实际维度。
- 空密钥保留原值；更换地址需明确填写新密钥。停用时换地址会丢弃旧密钥，避免以后误发到新目标；清除密钥需同时停用。
- 使用修订号拒绝过期表单；进程内锁与同目录原子替换避免半写配置。当前按单 API 进程运行，多进程并发写入不受跨进程锁保护。结果未确认时重新打开核对，不盲目重试。
- 对话一轮内固定聊天配置，下一轮使用新值；普通聊天、Agent 流与任务标题使用本机配置。自有 SDK 客户端退出时关闭；自定义服务不套用预置 DeepSeek 价格，费用保持未知。历史演示工具接口仍使用环境配置。

## 验收与复跑

修改配置加载会影响环境回退、聊天请求生命周期和任务标题；对应后端专项为 `apps/api/tests/model/test_local_model_settings.py`，聊天流回归为 `tests/chat/test_chat_stream.py`，原 Embedding 配置只选未配置、独立环境加载、禁止聊天凭证回退三例。所有供应商出口使用替身，无真实请求。

从根目录运行后端专项：

```bash
.venv/bin/python -m pytest -q apps/api/tests/model/test_local_model_settings.py
```

在 `apps/web` 运行公开协议、同源 BFF 和流指标专项：

```bash
node --experimental-strip-types --test test/features/model-settings/model-settings.test.ts test/features/chat/agent-stream.test.ts
node test/browser/model-settings/run.mjs
```

浏览器配置沿用 [环境说明](../ENVIRONMENT.md#5-定向测试与静态检查)。PC 专项编译实际组件与生产样式，用受控 BFF 检查 1366/1920 视口、键盘进入/Escape/焦点恢复、聊天无维度、检测与保存独立、重新打开、密钥不回显和修订冲突。证据在忽略目录 `apps/web/output/playwright/model-settings/`。ASGI、BFF 与实际浏览器组件分别验收，不代表真实供应商可用性或 Next/API 联合网络验收。
