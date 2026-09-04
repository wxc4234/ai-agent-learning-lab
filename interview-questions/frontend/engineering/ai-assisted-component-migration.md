# 如何使用 Codex 完成近百个文件的组件迁移，并控制 Agent 的改动风险？

> 主题：工程化 / AI Coding | 频率：中 | 关联项目：[ChatSearch AI](../../projects/chatsearch-overview.md)

## 考点（面试官在考察什么）

- 构建原理、依赖边界、缓存发布与可验证收益。

## 核心答案（能直接讲出口的版本）

我做过一次画布组件迁移：把 `chat-search` 中的表单、视频、图片等组件迁到 `chat-huabu`，改动接近一百个文件。Codex 主要辅助依赖梳理、文件迁移和机械修改。

我先提供原组件目录、组件注册表、`AssistantAPI` 定义及宿主实现四类上下文，要求 Agent 只输出依赖清单、不改代码。分析后确认真正难点不是移动文件，而是解除组件对 ChatSearch Store、对话方法、EventBus 和上报服务的反向依赖。

执行阶段限制改动范围，只允许修改 `chat-huabu`、ChatSearch 的 `AssistantAPI` 实现和注册脚本；服务端 component 名不能变化，组件不能反向引用 `chat-search`，PC、Wise 和历史回答行为必须保持一致。组件分批迁移，避免一次生成无法审查的大型 diff。

审查重点包括：检查残留反向依赖；比对迁移前后的组件注册名；确认事件监听在销毁时取消；验证每个组件仍会触发 `render-finished`，避免流式队列阻塞。

过程中发现 Agent 试图用通用 `AssistantAPI.on/off` 处理所有事件，但项目事件分属 assistant、component、conversation 等不同域。暂停视频和释放音频焦点如果套用同一接口，运行时会监听到错误的 EventBus。最终我改成 `onPauseVideoPlay`、`emitAbandonAudioFocus` 等语义化接口，并统一封装多种“新一轮对话开始”入口和取消订阅函数。

最后执行 type-check、lint、ChatSearch 构建和组件注册扫描，并回归 PC、Wise 与历史回答。Agent 负责扫描和机械修改；包边界、接口设计、风险判断和最终验收由我负责。

## 方法速记

`先梳理依赖 → 限制范围与不变量 → 分批修改 → 审查协议和生命周期 → 自动检查 + 多端回归。`

## 关键风险

- 文件迁移不等于完成解耦，必须消除对宿主实现的反向依赖。
- 类型通过不代表运行时正确，EventBus 事件域可能被 Agent 错误合并。
- component 名和 `render-finished` 属于协议契约，迁移时不能改变或遗漏。
