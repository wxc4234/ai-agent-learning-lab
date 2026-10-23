# Learning Lab：独立学习项目的面试证据入口

> 目标：AI 全栈开发工程师｜参考答案已整理、尚未模拟。
> 独立学习项目导航：本次仅恢复既有题目入口，未重新验收功能、测试或学习掌握情况。ChatSearch 工作项目另见 [AI 全栈面试题与知识核对](chatsearch-ai-fullstack-interview.md)。

这是本地优先 PC Coding Agent 学习项目，使用 Next.js、FastAPI、PostgreSQL、Redis 与 pgvector。它与工作项目 ChatSearch 分开陈述，不把这里的数据库、Sandbox、RAG 或测试结果套入公司后端架构。

## 可用的实践题

| 方向 | 已有题目与证据入口 | 面试边界 |
| --- | --- | --- |
| 运行状态与取消 | [AgentRun](../backend/web/agent-run-observability.md) | 执行所有权、取消终态、Redis 与 DB 非共同事务 |
| API 与数据建模 | [Task 模型](../backend/web/task-model.md) | 幂等创建、提交未知、BFF 响应契约 |
| 文件与工具权限 | [Workspace](../backend/web/workspace-directory.md) | 路径授权、事务外 I/O、重新授权、恢复边界 |
| RAG 与评测 | [检索指标](../ai/evaluation/code-retrieval-metrics.md) | 召回、拒答、冻结/留出及真实供应商边界 |

这里只做静态证据导航；课程进度、唯一下一课和未解决问题仍由 [LEARNING_HANDOFF](../../LEARNING_HANDOFF.md)维护，不复制另一份动态快照。本次题库维护不是课程完成验收，没有检查学习者未声明完成的课程改动。

## 怎样与工作经验连接

“工作项目中我接触了流式交互与任务状态问题；复盘 Go/Python 协作服务后，我进一步用独立学习项目实践 API、事务、工具权限和检索评测。”这是一条能力扩展路径，不代表后端工作模块均由本人实现。

个人陈述仍应逐项确认：本人实现、教练实现、共同调试、只读复盘。讲解时选能解释取舍和失败路径的模块，工程通过不等于已独立掌握。
