# ChatSearch 题库源码基线与职责边界

核对日期：2026-10-22。目标：AI 全栈开发工程师。以下是本机源码快照，不是线上版本认证，也不证明候选人亲自实现了全部模块。

## 仓库与版本

唯一版本表维护在[最新源码核对文档的项目与版本边界](chatsearch-ai-fullstack-interview.md#11-本次到底读了什么)。该文固定 2026-10-22 的 F/G/U/P 四个提交；P 使用已拉取的 `77d5befe4ff4f346857424a88176fadca9c95f8e`，旧 `11b23042` 仅用于历史对比，不再作为当前答案依据。2026-10-23 是题库恢复归档日期，不表示再次拉取或完成运行验证。

本页保留原 29 题使用的 F/G/U/P 证据编号；后续 Agent 执行、Checkpoint 与工具调度的具体行号和限制以最新核对文档为准。Learning Lab 是独立学习项目，进度与工程状态按仓库现有事实来源查阅，不从工作源码推断其实现。

## 证据定位

路径相对于上述版本表链接中的对应仓库。固定符号比易漂移行号更适合复查。P 的路径需用 `git show 77d5befe4ff4f346857424a88176fadca9c95f8e:<path>` 阅读，不能直接打开 master 工作区同名路径。

| 编号 | 路径与符号 | 2026-10-22 核对事实摘要 |
| --- | --- | --- |
| F1 | `packages/chat-search/src/client/component/chat-skill-panel/query.ts`：splitSkillQuery / prepareSkillQuery / readQuestionSkillSnapshot | UTF-16 半开区间、原文与派生文本、跨 TEXT、重答快照 |
| F2 | `packages/chat-search/src/client/service/skill-conversation.ts`：prepareSkillConversation | 上传后准备、复制快照、失败降级、显式空列表优先 |
| F3 | `packages/chat-search/src/client/service/chatstream.ts`：stopAllTaskModeBackgroundSSE / interruptTaskModeByContext | 断订阅与后端打断分开 |
| F4 | `packages/chat-stream/src/actions/request-actions.ts`：reconnect / ABORT_TYPE.TASK 分支 | 序号加一、重连初始化减一、任务辅助 SSE |
| F5 | `packages/chat-sse/src/chat-sse.ts`：trackSeqId / handleMessageData | 最大接收序号先于业务消费，不是提交水位 |
| F6 | `packages/chat-search/src/server/app.ts` 与 `controller/chat/hybrid.controller.ts` | 模板服务、manifest 启动缓存与缺失 503 |
| G1 | `model/service/agentapi/module.go`：Schedule | create/query、下游任务创建、io.Pipe、请求 Context |
| G2 | `model/service/agentapi/pump.go`：resolveStartID / flushMessages | 两种游标、写出后推进、坏包跳过、DONE 收口 |
| G3 | `model/service/streaming/handler.go`：Handle | XADD 成功后 ACK、重复 ID 错误、TTL 与近似裁剪 |
| G4 | `model/service/agentapi/interrupt.go`：handleInterrupt | 最新事件定位 AgentEndpoint 后转发 |
| G5 | `model/service/agentapi/timing_test.go`：TestResolveStartID / TestResumeStartIDEndToEnd | 本地已有游标边界测试源码；本次未运行 |
| U1 | `conf/exgraph/http_router.toml`：aichat_conversation_new.exg 路由 | 普通 conversation 使用执行图入口 |
| U2 | `model/dao/chat_record.go`：ReadRecordByMsgID | uid/session_id/message_id 联合过滤 |
| U3 | `model/dao/chat_topic.go`：DeleteTopicBySessionID / deleteTopicBySessionID | Repeatable Read 事务内删除 topic 和 record |
| U4 | `model/dao/tx_base.go`：TxBaseUpdate | 显式 QueryExecutor、RowsAffected |
| P1 | `pl_agent/agent/asservice/mcp/polling.py`：list_tools / _submit_create_task | 新建任务 copy_context 后线程池提交；HTTP 入口还需区分 general SSE 与 native resume 分流，详见最新核对 Q1 |
| P2 | `biz/aichat/skill/agent.py`：execute | 业务执行、legacy/native 与恢复归路、BOS 或内存回退；详见最新核对 Q2/Q5/Q8 |
| P3 | `biz/aichat/skill/nodes/react_agent.py`：build_react_agent | 工具装配与计划、压缩、流式等 middleware |
| P4 | `biz/aichat/skill/checkpoint/bos_checkpointer.py`：put / put_writes / repersist | 普通步骤内存写、根 interrupt 持久化、收尾重存 |
| P5 | `pl_agent/servicer/pbrpc/agent_event/__init__.py`：open_for_resume | 不重发首包、从保存序号后继续发事件 |

## 需要纠正的旧口径

- “checkpoint 就是 Redis 从这个 ID 后读”：要区分 body 的下一条序号和 Header 的上一条序号；详见[全栈任务题](chatsearch-fullstack-interview.md)。
- “XADD 成功就是磁盘持久化且 exactly-once”：这里只证明 Redis 接受写入，持久化/复制策略需另核对；ACK 丢失与重复写仍有窗口。
- “网关背压能限制模型生成”：当前 Redis 介于生产与消费之间，慢浏览器不能自动暂停独立 Agent。
- “pl-agent 都走 BaseAgent.graph”：业务覆写 execute，不能用框架默认路径推断业务。
- “接入 create 都只是后台线程池提交”：普通任务提交、general SSE 与 native resume 拦截要分开；名称与旧注释不能代替实现。
- “有 Checkpointer 就任意时刻跨机恢复”：P4 只在特定挂起点持久化，P2 还会回退内存。

## 面试表达与验证边界

工作项目可以讲已确认的个人前端交付；三个后端仓库默认属于协作理解与源码复盘，使用“我沿调用链核对到……”的表述。个人负责范围、线上规模、收益数字均待本人补证，不从代码存在推断。

学习项目是本地优先 PC Coding Agent，FastAPI / Next.js / PostgreSQL / Redis / pgvector 的实践不得冒充工作项目的技术栈或线上经历。教练实现、工程通过、独立掌握分开记录。

源码核对与本次恢复只包含阅读、题库整理与文档校验，未执行跨服务集成测试、压测、生产查询或现有业务测试。新题状态统一为“参考答案已整理、尚未模拟”；故障矩阵是建议验收，不是已通过报告。
