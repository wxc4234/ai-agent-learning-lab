# AI Agent Learning Lab

面向求职的 AI Agent 应用开发学习仓库。目标是在 3 个月内，从前端开发转向 **AI Agent 应用开发工程师（偏前端 / 全栈产品工程）**，独立开发、测试和部署一个可观测、可恢复、可安全执行代码任务的 Codex-like Coding Agent。

## 从这里开始

1. [12 周学习计划](LEARNING_PLAN.md)：唯一的完整学习路线。
2. [逐日大纲与验收标准](LEARNING_CURRICULUM.md)：每天做什么、做到什么程度算完成、常见坑和面试题库。
3. [当前进度与学习交接](LEARNING_HANDOFF.md)：今天学到哪里、下一课是什么。
4. [Codex-like 能力边界](docs/codex-like-agent-scope.md)：最终产品必须具备什么、如何验收、明确不做什么。
5. [新会话教学指南](LEARNING_COACH_GUIDE.md)：新的学习会话如何接续、讲解、验收和更新进度。
6. [岗位与开源项目技能校准](SKILL_GAP_ANALYSIS.md)：为什么选择这些技术栈。
7. [环境安装与启动](ENVIRONMENT.md)：macOS/Windows 配置和常见问题。
8. [每周学习记录](week-learning/README.md)：已经结束周次的练习与复盘材料。
9. [面试题库](interview-questions/README.md)：AI 全栈社招面试题库（算法 / 前端 / 后端 / AI / 系统设计 / 项目 / 行为面试）。

当前阶段（2026-09-10）：日历处于**第 3 周（09-07 ～ 09-13）**，第 1 ～ 3 周验收均已完成，正式周进度为 **3 / 12（25%）**。Tool Registry、参数校验和基础 Agent Loop 已正式归入第 3 周的最小 Runtime，不再误记为提前进入第 7 周；第 4 周的可观测与 Token 预算核心已预完成。当前先收尾失败运行摘要，再从第 4 周 Day 1 的身份与会话开始顺序推进。

节奏约定：每日 4 ～ 6 小时；作品集先做一个足够深的主项目；**第 8 周开始第一批投递，不等作品集全部完成**。路线于 2026-08-31 按最新岗位调研修正过，依据见 [SKILL_GAP_ANALYSIS.md](SKILL_GAP_ANALYSIS.md) 第 10 节。

> [!IMPORTANT]
> `week-XX/` 只保存单周记录和一次性练习。任何会跨周持续开发的源码、配置、测试、环境说明或基础设施必须放在 `apps/`、`packages/`、`infra/` 或仓库根目录，不能放进某一周的目录。完整约束见 [AGENTS.md](AGENTS.md)。

## 当前项目

当前跨周项目已经包含：

- FastAPI + DeepSeek 多轮对话。
- Docker Compose 管理 PostgreSQL + pgvector、Redis 的本地环境。
- PostgreSQL + pgvector 会话持久化。
- FastAPI + DeepSeek 流式聊天接口。
- Next.js Agent 前端与同源 BFF 流代理。
- Tool Calling 和本地工具白名单。
- 有状态 DeepSeek 决策适配层与通用 Agent Loop。
- 正式聊天的结构化 Agent 事件流与前端工具执行卡片。
- 单次 Agent 运行的 Token、模型/工具耗时与人民币费用估算。
- pytest 自动化测试：模型消息协议、工具、仓储与接口错误契约。
- Swagger 多步骤 Tool Calling 验证接口。

## 快速启动

在完成环境安装后运行：

```bash
cd apps/api
python -m uvicorn app.main:app --reload
```

然后打开 <http://127.0.0.1:8000/docs>。

## 主要目录

```text
ai-agent-learning-lab/
├── README.md                    # 项目入口
├── AGENTS.md                    # 长期有效的仓库与学习规则
├── ENVIRONMENT.md               # 跨周环境安装、启动和排错
├── LEARNING_PLAN.md             # 12 周唯一主计划
├── LEARNING_CURRICULUM.md       # 逐日大纲、验收标准与面试题库
├── LEARNING_HANDOFF.md          # 当前进度和下一课
├── LEARNING_COACH_GUIDE.md      # 新会话的教学与接续规则
├── SKILL_GAP_ANALYSIS.md        # 岗位与开源项目分析
├── requirements.txt             # Python 依赖
├── apps/
│   ├── api/                     # 持续演进的 FastAPI Agent 后端
│   │   └── app/                 # API 源码包：路由、服务、仓储和工具
│   └── web/                     # 第 3 周加入的 Next.js Agent 前端
├── docs/                        # 架构、事件协议与 Codex-like 最终能力边界
├── interview-questions/         # AI 全栈社招面试题库（见上第 9 点）
└── week-learning/               # 各周结束后的记录和一次性练习
    ├── README.md                # 每周记录索引与归档规则
    ├── week-01/                 # 第 1 周记录和练习
    ├── week-02/                 # 第 2 周复盘
    └── week-03/                 # 第 3 周复盘
```
