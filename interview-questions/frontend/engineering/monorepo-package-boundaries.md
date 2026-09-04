# monorepo 中如何划分跨包职责，并避免一次环境适配破坏其他端？

> 主题：工程化 / Monorepo | 频率：高 | 关联项目：[ChatSearch AI](../../projects/chatsearch-vite-rolldown.md)

## 考点（面试官在考察什么）

- 构建原理、依赖边界、缓存发布与可验证收益。

## 核心答案（能直接讲出口的版本）

跨包需求先按**输入、对话状态、内容渲染和业务编排**划分职责：`chat-input-next` 只提供输入能力和事件；`chat-stream` 维护通用 Prompt、QAPair 和请求生命周期；`chat-huabu` 负责 Block 与业务卡片渲染；`chat-search` 负责组合这些能力，并适配 PC、Wise、Native 和 Hybrid。

荣耀 SDK 接入是一个实际例子。首先在 `chat-util` 新增 `isHonorSdk()`，同时要求 UA 包含 `honorbrowser` 和 `bdboxengine`，避免把其他使用通用 SDK 标识的环境误判为荣耀；再将荣耀从普通移动浏览器判断中排除，并为 EventBus 增加 `hnbrowser` Native 通道映射。

`chat-search` 负责历史模式、登录提示、分享、图片预览和 Native 事件等宿主适配；`chat-huabu` 则按具体能力降级，例如分享卡片可以按端内处理，但音乐播放器仍排除荣耀，因为它没有完全支持手百的音频控制能力。此次没有修改输入协议、SSE 或 QAPair，所以 `chat-input-next` 和 `chat-stream` 不需要改动，避免环境判断向通用包扩散。

联调发现的核心问题是：旧逻辑把荣耀 Wise WebView 当成普通移动浏览器，导致 EventBus 没走 Native 数据通道；但若直接等同于手百，又会调用荣耀不支持的登录、播放器等能力。最终通过**独立环境标识、正确通信 action 和能力级降级**解决。

验证时补充荣耀 SDK UA，执行 PC、Wise、Wise-Speed/Hybrid 构建和端内交互回归。这个需求中，我实际负责环境识别、EventBus 通道适配以及 ChatSearch/Huabu 的能力兼容层。

## 分层速记

`chat-input-next 管输入；chat-stream 管对话状态；chat-huabu 管内容渲染；chat-search 管业务组合和多端适配。`

## 容易说错的地方

- `bdboxengine` 是通用 SDK 标识，不能单独用于判断荣耀环境。
- 新增一种端环境不等于把它整体视为手百，应按具体能力判断和降级。
- 跨包需求不代表所有包都要修改；未改变输入和对话协议时，应保持公共包稳定。
- 当前仍有深层导入、反向类型依赖和 Hybrid 独立 E2E 缺失等工程边界，不能声称绝对不会回归。
