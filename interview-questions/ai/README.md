# AI / LLM 原理

后续执行细节与新旧版本区别见[Agent 执行、Checkpoint 与工具调度核对](../projects/chatsearch-ai-fullstack-interview.md)（源码核对 2026-10-22，P：77d5befe）。本页仅作分类导航，答案在对应专题维护。

## Agent 工程重点

[Agent Loop、Checkpoint 与工具副作用](agent/checkpoint-and-tool-effects.md)：5 道核心追问，关联工作项目 pl-agent 的本地业务快照，并单独链接 Learning Lab 的运行与评测证据。P0；参考答案已整理、尚未模拟。

社招 AI 岗的**核心区分项**。这里补的是 `LEARNING_CURRICULUM.md` 没覆盖的**原理层**问题——Transformer、微调、推理部署、评测等；应用层任务与验收导航见课程大纲；本目录维护可复习的完整答案，并区分工作项目源码与学习项目证据。

## 主题分类

- `foundation/` 大模型基础（Transformer / 注意力机制 / 位置编码 / Tokenizer / 采样策略）
- `finetuning/` 微调（LoRA / QLoRA / PEFT / SFT / RLHF / DPO / 数据构造）
- `rag/` RAG 原理（chunking / embedding / 检索 / rerank / 评估指标）
- `agent/` Agent 原理（ReAct / Planning / Memory / Tool Use / 多智能体，与学习计划联动）
- `prompt/` Prompt Engineering（CoT / few-shot / 结构化输出 / 防注入）
- `vector-db/` 向量数据库（HNSW / 索引 / pgvector vs Milvus vs Pinecone）
- `inference/` 推理与部署（vLLM / 量化 / KV Cache / 延迟·成本优化 / 流式）
- `evaluation/` 评测（LLM-as-Judge / 检索指标 vs Agent 轨迹评测 / 回归）
- `multimodal/` 多模态（VLM / 图像理解 / 可选）

## 题解模板

```markdown
# 题目

> 主题：xxx | 频率：高/中/低 | 关联项目：xxx

## 考点（面试官在考察什么）

## 核心答案（讲取舍）

## 结合项目怎么讲

## 追问清单

## 延伸 / 坑
```

## 题目索引

| 主题 | 题目 | 频率 | 状态 |
| --- | --- | --- | --- |
| foundation | **[必问]** Transformer 结构 / 自注意力为什么除以 √d | 高 | 待做 |
| finetuning | LoRA 原理，为什么低秩就够 | 高 | 待做 |
| rag | 召回质量差的排查顺序 | 高 | 待做 |
| inference | 大模型推理慢，从哪些维度优化 | 高 | 待做 |
| evaluation | LLM-as-Judge 的偏差与校准 | 高 | 待做 |
| evaluation | [代码检索指标高为什么仍可能找不全来源或答错](evaluation/code-retrieval-metrics.md) | 高 | 参考答案已整理、尚未模拟 |
