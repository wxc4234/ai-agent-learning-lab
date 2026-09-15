# Codex-like Coding Agent：能力边界与最终验收

更新时间：2026-09-15（Asia/Shanghai）

## 1. 产品目标

构建一个本地优先的 PC Coding Agent。用户在本机启动 Web UI 与 Runtime，自行配置模型，无需注册登录，创建或导入 Workspace、提交代码任务，并观察 Agent 从理解上下文、制定计划、调用工具、修改代码、运行验证到交付 Diff 的完整过程。

这里的 “Codex-like” 是工程能力目标，不是品牌或内部实现复刻：项目不训练基础模型，也不声称复制 OpenAI 的专有提示词、推理系统、隔离平台或云调度架构。

公开能力校准来源：

- [Responses API](https://developers.openai.com/api/reference/cli/resources/beta/subresources/responses)：工具、Shell、Apply Patch、MCP、流式事件、Compaction、Steering 与运行状态。
- [Evals API](https://developers.openai.com/api/reference/java/resources/evals/methods/create)：用固定数据源与测试标准评价 Agent 工作流。
- [Skills API](https://developers.openai.com/api/reference/python/resources/skills/methods/create)：把可复用指令与资源封装为版本化能力。

### PC 工作台布局目标

主要视觉参考已由用户确认：2026-09-15 提供的 Codex 截图；补充参考 https://github.com/deepseek-ai/deepseek-harness 。采用浅灰绿色项目侧栏、白色留白对话区、底部圆角输入与紧凑右侧详情；本课先实现布局、收起和现有运行详情，调宽、真实项目树及绑定入口分课推进。

最终 PC 界面采用用户指定的 DeepSeek Harness / Codex Harness 风格三栏工作台；顶部“聊天首页 / 工作空间”为临时导航。布局规划为左侧项目/任务、中间对话与执行过程、右侧文件/Diff/运行详情，支持侧栏收起与调宽；具体视觉实现前核对用户所指参考版本，不把临时独立页面当最终布局。

现阶段先完成目录绑定基础；接入工作台布局时迁移现有聊天、Workspace 与创建入口，后续能力逐步填入三栏，不持续扩展顶部临时导航。

## 2. 核心能力矩阵

| 能力域 | 最小产品能力 | 可验证证据 |
|---|---|---|
| 身份与隔离 | 本机稳定身份、内部访问凭证及 Workspace 文件/任务边界；账号模式保留 | 未授权请求被拒绝，不接管历史账号资源，目录越界被拒绝 |
| 任务与状态 | Task 与 Run 分离；支持 queued/planning/running/waiting_approval/done/aborted/error | 刷新页面后状态和时间线一致 |
| 流式交互 | 文本、计划、工具、终端、补丁、审批和终态使用稳定事件协议 | 任意网络分块可解析；每次运行只有一个终态 |
| Agent Loop | 模型只产生 ToolAction 或 FinalAnswer；Observation 回灌；有步数、时间和 Token 上限 | Mock 轨迹覆盖成功、纠错、超时、取消和预算耗尽 |
| 文件与搜索 | 列目录、读文件、关键词/符号搜索，全部限制在 Workspace 内 | 路径穿越、软链接逃逸和大文件被拦截 |
| Shell 与测试 | 在隔离 Worker 中执行命令，限制 cwd、环境变量、时间、资源和输出 | 超时、非零退出、取消与输出截断均有结构化结果 |
| Apply Patch 与 Git | 结构化补丁、原子写入、Git status/diff、测试后交付 | 失败补丁不留下半写文件；UI 可审查 Diff |
| 审批与策略 | 高风险工具必须等待服务端批准；支持批准、改参批准、拒绝 | 绕过前端仍无法执行；原始与最终参数均被审计 |
| Context Engineering | 合并仓库指令、任务、历史、代码检索和工具结果，并按预算裁剪/压缩 | 可查看上下文来源、Token 占比与被裁剪原因 |
| 代码检索 | 关键词/符号/向量混合检索、权限过滤、引用和调试信息 | 固定跨文件任务集输出 Recall/MRR/引用正确率 |
| Durable Runtime | Checkpoint、暂停、恢复、重试、幂等和服务重启恢复 | 在工具前、审批中和失败后中断都能继续 |
| MCP / Skills | 动态接入 MCP 工具；仓库级指令与 Skill 可发现、版本化和审计 | 自建 MCP Server 与至少一个 Skill 被真实任务调用 |
| 有限多 Agent | 只委派独立、边界清楚的子任务，限制并发并由主 Agent 合并结果 | 并发检索/测试任务不产生重复写入或状态竞争 |
| 可观测与评测 | Trace、Token、成本、延迟、工具成功率、补丁正确率和轨迹评分 | CI 对固定任务集执行回归门禁 |
| 生产化 | 后台 Worker、限流、并发控制、日志脱敏、健康检查、部署与恢复 | 干净 PC 完成端到端演示，README 可复现安装/启动/升级；域名展示与分发 |

## 3. AI 全栈掌握范围

| 层 | 必须掌握 |
|---|---|
| 浏览器与前端 | React/Next.js、BFF、流式读取、状态机、任务/文件/Diff/终端 UI、错误恢复、可访问性 |
| API 与业务 | FastAPI、Pydantic、认证授权、事务、幂等、限流、后台任务、领域事件 |
| Agent Runtime | 模型适配、Tool Calling、ReAct、计划、记忆、反思、Checkpoint、取消、Steering、预算 |
| 工具执行 | 文件系统边界、Shell/Sandbox、Apply Patch、Git、测试、审批与审计 |
| 数据与检索 | PostgreSQL、SQLAlchemy、Alembic、Redis、pgvector、混合检索、Rerank、对象存储 |
| 质量与运维 | pytest、前端测试、Evals、Trace、CI/CD、Docker、监控、安全边界和本地交付 |

## 4. 最终端到端验收场景

1. 用户本机启动免登录工作台，配置自己的模型并选择样例 Git Workspace；其他目录和原账号资源不会被接管。
2. 用户提交“修改两个相关文件并补测试”的任务。
3. Agent 读取仓库指令，检索相关符号和代码，展示一个可更新的计划。
4. Agent 调用文件、搜索、补丁和测试工具；浏览器实时展示工具状态、终端输出和成本。
5. 遇到高风险命令时进入 `waiting_approval`；用户可以批准、修改参数后批准或拒绝。
6. 第一次测试失败后，Agent 根据 Observation 修正补丁并再次验证。
7. 最终界面展示回答、代码引用、测试结果和 Git Diff，用户可以审查改动。
8. 在执行中刷新浏览器或重启 API/Worker，任务能从持久化状态恢复，且不会重复执行已提交的副作用。
9. 固定任务集能输出任务成功率、补丁正确率、工具选择正确率、平均步骤、成本和 P95 延迟。

以上九项全部通过，才算完成主项目；仅有聊天、单次 Tool Calling 或代码生成文本不算完成 Codex-like Agent。

## 5. 明确不做

- 不训练或微调基础模型，不实现推理引擎。
- 不承诺达到 Codex 的模型能力、代码质量或云端规模。
- 不在 12 周内深挖 Kubernetes、完整虚拟机平台或跨区域调度。
- 不同时维护两套 Agent 框架；主线使用手写 Runtime 理解原理，再用 LangGraph 实现持久状态图。
- Computer Use、A2A 和第二框架只做概念或可选演示，不阻塞主项目验收。
