# AI Agent Learning Lab

面向求职的 AI Agent 应用开发学习仓库。目标是在 3 个月内，从前端开发转向 **AI Agent 应用开发工程师（偏前端 / 全栈产品工程）**，能够独立开发、测试和部署完整 Agent 产品。

## 从这里开始

1. [12 周学习计划](LEARNING_PLAN.md)：唯一的完整学习路线。
2. [逐日大纲与验收标准](LEARNING_CURRICULUM.md)：每天做什么、做到什么程度算完成、常见坑和面试题库。
3. [当前进度与学习交接](LEARNING_HANDOFF.md)：今天学到哪里、下一课是什么。
4. [岗位与开源项目技能校准](SKILL_GAP_ANALYSIS.md)：为什么选择这些技术栈。
5. [环境安装与启动](ENVIRONMENT.md)：macOS/Windows 配置和常见问题。
6. [第 1 周完成记录](week-01/README.md)：已经完成的基础内容。

当前阶段：**第 2 周，补齐支撑 Agent 产品开发所需的后端工程底座。** 第 3 周进入 Next.js Agent UI，并同步建立 run_id 事件落库与冒烟评测。

节奏约定：每日 4 ～ 6 小时；作品集先做一个足够深的主项目；**第 8 周开始第一批投递，不等作品集全部完成**。路线于 2026-08-31 按最新岗位调研修正过，依据见 [SKILL_GAP_ANALYSIS.md](SKILL_GAP_ANALYSIS.md) 第 10 节。

> [!IMPORTANT]
> `week-XX/` 只保存单周记录和一次性练习。任何会跨周持续开发的源码、配置、测试、环境说明或基础设施必须放在 `apps/`、`packages/`、`infra/` 或仓库根目录，不能放进某一周的目录。完整约束见 [AGENTS.md](AGENTS.md)。

## 当前项目

`apps/api` 中已经包含：

- FastAPI + DeepSeek 多轮对话。
- SQLite 会话持久化。
- Tool Calling 和本地工具白名单。
- Swagger 测试接口。

## 快速启动

在完成环境安装后运行：

```bash
cd apps/api
python -m uvicorn main:app --reload
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
├── SKILL_GAP_ANALYSIS.md        # 岗位与开源项目分析
├── requirements.txt             # Python 依赖
├── apps/
│   ├── api/                     # 持续演进的 FastAPI Agent 后端
│   └── web/                     # 第 3 周加入的 Next.js Agent 前端
└── week-01/                     # 仅保存第 1 周记录和一次性练习
    └── README.md                # 第 1 周完成记录
```
