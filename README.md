# AI Agent Learning Lab

这是一个面向求职的 AI Agent 应用开发学习仓库。

目标是在 **3 个月内**，从前端开发逐步转向能够独立开发、调试和部署 AI Agent 应用。

## 从这里开始

- [第 1 周学习计划](week-01/README.md)
- [环境安装、项目启动和常见问题](week-01/环境与依赖.md)
- [跨电脑学习交接与后续路线](LEARNING_HANDOFF.md)

## 当前进度

当前处于第 1 周：**Python + FastAPI + LLM 最小闭环**。

- [x] Python 基础、异常处理、JSON 和类
- [x] FastAPI 路由、请求体、响应体和错误处理
- [x] 调用 DeepSeek API 获取真实模型回复
- [x] 使用 `session_id` 隔离多轮对话
- [x] 最近 5 轮滑动窗口和内存清理
- [x] 使用 SQLite 保存并恢复对话历史
- [x] 完成首个 Tool Calling 循环：模型选工具、Python 执行、模型组织答案
- [ ] 实现带参数工具和通用工具调度器（下一步）

## 项目结构

```text
ai-agent-learning-lab/
├── .env.example                 # 环境变量模板
├── requirements.txt             # Python 依赖
└── week-01/
    ├── README.md                 # 第 1 周学习计划
    ├── 环境与依赖.md             # 安装、启动与排错
    └── fastapi_app/
        ├── main.py               # FastAPI CRUD 练习
        ├── chat_api.py           # DeepSeek 多轮对话接口
        ├── database.py           # SQLite 对话持久化
        └── agent_tools.py        # Agent 工具定义与函数映射
```

## 快速启动

完成环境安装后，在项目根目录运行：

```bash
cd week-01/fastapi_app
python -m uvicorn chat_api:app --reload
```

然后打开 Swagger：<http://127.0.0.1:8000/docs>。

如果是首次运行、切换电脑或遇到解释器问题，请查看[环境与依赖](week-01/环境与依赖.md)。

## 当前下一步

实现带参数工具，解析模型生成的 JSON 参数，并逐步把工具调用接入带记忆的 `/chat` 主接口。
