# AI / LLM 原理

社招 AI 岗的**核心区分项**。这里补的是 `LEARNING_CURRICULUM.md` 没覆盖的**原理层**问题——Transformer、微调、推理部署、评测等；应用层的 Agent/MCP/RAG pipeline 题继续看那份文件的第 4 章。

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
