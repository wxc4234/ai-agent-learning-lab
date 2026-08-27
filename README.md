# AI Agent Learning Lab

> 目标：在 3 个月内完成从前端开发到 AI Agent 应用开发的转型。

## 当前进度

**第 1 周：Python + FastAPI 最小闭环（2026-08-24 ～ 2026-08-30）**

学习顺序：

```text
Python 基础 → FastAPI 接口 → 调用 LLM → 保存代码 → 写学习复盘
```

## 本周入口

- [查看第 1 周学习计划](week-01/README.md)
- [环境与依赖安装说明](week-01/环境与依赖.md)
- 每天完成一个可提交成果，不追求一次学完全部知识。
- 本周验收：能够独立运行一个 FastAPI 接口，并通过接口调用一次 LLM。

## 环境准备

依赖统一写在 [`requirements.txt`](requirements.txt)，用 `uv` 安装（`.venv` 里没有 pip）：

```bash
.venv\Scripts\activate
uv pip install -r requirements.txt
```

调用 LLM 前，需在项目根目录创建 `.env`（参考 [`.env.example`](.env.example)）并填入 `DEEPSEEK_API_KEY`。更多说明见 [环境与依赖安装说明](week-01/环境与依赖.md)。

## 仓库使用方法

1. 每天开始前先查看当天任务。
2. 在对应目录完成代码。
3. 完成后提交一次 Git commit。
4. 在学习记录中写下：今天完成了什么、遇到了什么问题、明天做什么。
