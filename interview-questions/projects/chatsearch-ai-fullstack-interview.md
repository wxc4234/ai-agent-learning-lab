# ChatSearch：AI 全栈面试题与相关知识核对

源码核对日期：2026-10-22（Asia/Shanghai）；仓库恢复归档：2026-10-23\
目标岗位：AI 全栈开发工程师\
状态：参考答案已整理，尚未模拟面试；源码静态核对完成，核对与恢复均未执行业务测试、压测或线上验证。

来源：ChatSearch 工作项目与协作后端源码复盘。本文在仓库内持续维护；桌面文件仅保留恢复副本。原 29 题分类入口见[项目索引](README.md)，符号证据见[源码基线导航](chatsearch-source-baseline.md)，独立学习实践见[Learning Lab 证据](learning-lab-evidence.md)。

## 阅读入口

1. 先看“项目与版本边界”，明确哪些是工作项目、哪些只是学习经历。
2. 用“项目讲述”组织开场，再从 Agent 执行、Checkpoint、工具调度各选两题深挖。
3. “相关知识”解释可迁移的原理；“失败场景清单”用于准备验证思路，不是已通过的测试报告。
4. 源码证据都固定到本次核对的提交。文中“当前”仅指该源码快照，不保证生产环境已经启用对应配置。

## 一、项目与版本边界

### 1.1 本次到底读了什么

| 标识 | 仓库与读取方式 | 固定提交 | 范围 |
| --- | --- | --- | --- |
| P | `/Users/wanxiancheng/pl-agent/baidu/ps-se/pl-agent`，通过 `git show` 读本地已拉取的 `origin/aichat-task` | `77d5befe4ff4f346857424a88176fadca9c95f8e`，2026-10-22 | 本次核心：Agent 执行、Checkpoint、工具、RSI |
| F | `/Users/wanxiancheng/chat-search/baidu/ps-se-fe-tpl/chat-search` | `4638c04c4162cd7b1e492db1d99ce7f0f2159107` | 浏览器续传、技能坐标、断订阅与停止的区别 |
| G | `/Users/wanxiancheng/gateway/baidu/ps-se-go/agent-gateway` | `f9ad9e1904d51d9d76105ac1c7da2da4b8d25b86` | Redis Stream、SSE 起点换算与 ACK |
| U | `/Users/wanxiancheng/assistant-uigo/baidu/mms-voice/assistant-ui-go` | `10a3c7a0c709d1accbc35396303d4b62685e1f0c` | 历史会话的数据访问与事务边界参考 |

2026-10-22 核对时，P 工作区在 master `b8dc4e53`，框架主分支不作为任务业务实现的替代。读取远程跟踪引用不需要切换分支。上轮用的旧业务提交是 `11b23042`（2026-09-15）；本次既读最新业务源码，也比较这两个提交，避免把早已存在的机制说成刚新增。

G、U 是本机可用版本，未证明与 P 对应同一部署批次。F 的未提交环境识别、测试和锁文件改动未作为已上线成果。本次没有改动这些业务仓库。

证据表示法：`P:path/to/file.py:123` 表示 P 固定业务提交内的第 123 行，不表示 master 工作区对应行。复查方式：

```bash
git -C /Users/wanxiancheng/pl-agent/baidu/ps-se/pl-agent show 77d5befe4ff4f346857424a88176fadca9c95f8e:biz/aichat/skill/agent.py | nl -ba
```

### 1.2 工作与学习分别怎么说

| 类型 | 可使用的说法 | 需要避免 |
| --- | --- | --- |
| 工作项目个人交付 | 说明本人实际负责的前端功能、决策、排障和协作，准备提交或需求证据 | 仅凭源码存在就说“这个后端是我设计的” |
| 工作后端源码理解 | “我沿任务链路核对了网关、执行、状态恢复和工具边界” | 把源码复盘改写成后端生产负责人经历 |
| Learning Lab 学习实践 | 单独介绍本地 Coding Agent、FastAPI/Next.js/PostgreSQL/Redis/pgvector 的历史实践，注明本人或教练完成 | 把学习项目栈、测试结果、召回指标套到 ChatSearch |
| 设计建议 | “如果需要更强保证，我会增加……” | 把建议当成已经上线的实现 |

2026-10-22 初次整理时未能读取原学习仓库；2026-10-23 恢复题库时，已确认 Learning Lab 仓库存在并检出 main。本次只恢复题库和导航，没有重新验收学习项目实现；学习进度由 `LEARNING_HANDOFF.md` 维护，已有实践按[独立学习证据](learning-lab-evidence.md)查阅。个人职责、线上规模、收益数字仍需要本人确认。

### 1.3 对上轮内容的修正原则

- Agent 不只有“线程池里跑 Loop”一条路径：需要区分创建、legacy/native 灰度、DB 归路恢复和 claimed 执行。
- Supervisor、挂起持久化、内存回退、收尾 repersist、工具 detach 取消等机制在旧提交已经存在；本次是重新核对和补全解释，不能包装成本次更新才出现。
- 最新提交包含 RSI 执行侧评估能力，以及观测、工具契约和结果处理变化。不能从“增加了评估代码”推断质量已经提升。
- “请求已接受”“信号已投递”“执行已结束”“副作用已确认”必须分开；控制面状态、事件序号、UI 完成也不能互相替代。
- 上轮对后端的简化答案由本文覆盖；未重新检查的外围功能不宣称全面更新或全面验收。

### 1.4 旧快照到最新业务快照：哪些真的变了

| 主题 | 相对 11b23042 的核对结论 | 面试题如何更新 |
| --- | --- | --- |
| Supervisor / native-legacy / claimed 恢复 | 旧版已有，本轮重新核实 | 讲清分流与边界，不宣称新架构刚上线 |
| BOS interrupt 持久化 / repersist / MemorySaver | 核心机制旧版已有，新增物理操作观测等 | 补全存在性与内容版本、底层异常分类的限制 |
| 工具取消与子 Agent 线程池 | detach 与池机制旧版已有 | 取消不等于终止副作用，进程容量不等于集群预算 |
| RSI | 新增版本化覆盖、校验、执行侧环境还原与 guard | 以实验准备能力表述，不宣称完整自进化闭环 |
| Resource Finder | 移除 resource_assessment 等旧限制，保留一次/互斥/混批拒绝 | 删除旧门禁答案，按当前执行条件说明 |
| load_skill | 从多个 names 改为单个 name 的加载契约 | 说明单次加载、去重与持久状态，不混用旧参数 |
| 工具事件与执行观测 | 补早退 start/end、call_id 和执行段信息等 | 把模型结果、UI 收口、日志关联分别验证 |

配置中的 durability="exit" 在差异中主要是位置调整，不能单独当作新增持久化保证。

## 二、项目讲述

### 30 秒版本

ChatSearch 是多端 AI 搜索与助手产品。我会先说明自己实际负责的交互与前端链路，再解释协作服务：浏览器用 SSE 接收回答和任务状态，任务网关通过 Redis Stream 将连接与后台执行解耦，Python Agent 负责工作流和工具。后端复盘中，我重点关注创建与恢复如何归路、Checkpoint 保存什么、工具超时与取消是否真的停止副作用。独立学习项目则用于说明我在 API、数据库、工具权限与检索评测方面的实践。

### 链路速记

```text
浏览器：问题、技能快照、稳定轮次身份
    → Go 任务网关：创建或订阅恢复
        → Python 接入：校验、创建/恢复分流
            → legacy 或 native/Supervisor 执行路径
                → 工作流/模型/工具/middleware
                → 挂起状态与执行控制面
                → 事件上报
        → Redis Stream → SSE 回放
    → 前端业务状态与渲染完成
```

这张图描述核对到的协作关系，不表示几个本地版本已完成本轮联合测试。普通 conversation 的后端路由与任务模式也不能混为一条。

## 三、Agent 执行：项目面试题

### Q1｜创建任务返回成功，究竟成功了什么？〔P0〕

**参考回答：** 对于普通新建任务路径，接入层先做请求转换、预处理与校验，再把执行提交到后台，随后返回 task_id。同一 HTTP 入口还会等待路由决策，通用意图分支可返回 SSE；native resume 也可能被拦截处理而不提交普通 runner。真正新建任务的 `agent.execute` 在 runner 里运行，所以创建成功不等于模型完成、工具成功或用户收到完整结果。要把请求接受、任务运行、终态持久化和前端交付作为不同阶段观察。

**代码事实：** `_submit_create_task` 使用 `contextvars.copy_context()`，通过 `TaskExecutor.submit(parent_ctx.run, _runner)` 派发。TaskExecutor 配置 40 个线程。它限制同时运行的线程，不等于系统只接收 40 个任务，更不等于持久任务队列。

**追问：流量暴增怎么办？** 先测队列等待、活跃任务与模型配额，再设计准入、总预算、排队期限和拒绝策略。代码中线程池大小不能替代这些业务限制。创建回执丢失时也不能无条件重试，否则可能启动第二次执行。

**证据：** `P:pl_agent/agent/asservice/mcp/polling.py:409`、`:435`、`:517`、`:630`、`:914`；`P:pl_agent/bootstrap.py:226`。\
**验证思路：** 校验失败、提交失败、排队超时、提交成功后响应丢失、后台初始化失败分别检查，不只断言创建接口 200。

### Q2｜Workflow、ReAct Agent 和 Supervisor 分别管什么？〔P0〕

**参考回答：** Workflow 表达节点依赖和业务路由，ReAct Agent 负责模型决策与工具反馈循环，Supervisor 负责执行驱动与控制面协作。主图包含并行准备和条件路由，不能画成所有节点无条件顺序执行。当前还有 legacy/native 双路径；存在 Supervisor 集成，不代表所有请求都启用了它。

**代码事实：** 主图从 `rsi_prepare` 进入请求准备与 fan-out；`react_agent` 使用 ADK `create_agent`。首轮 create 的分布式门控排除热点、cron 和 resume 等场景，并按 thread_id 稳定分桶；resume 的 legacy/native 归路由 DB 归属单独决定，不能据此说恢复不走分布式。legacy 仍走 invoke/ainvoke。热点图单独组织，未挂同样的 checkpointer。

**追问：分布式失败能回退吗？** 当前针对识别出的 DB 故障，并且尚未输出首 token 的场景允许回退。业务错误或已输出 token 时不应一律重跑。首 token 前没有工具副作用是当前执行顺序的假设，增加前置工具后必须重新审计，不能视为通用定律。

**证据：** `P:biz/aichat/skill/agent.py:2170`、`:2252`、`:2323`、`:2431`、`:2548`；`P:biz/aichat/skill/nodes/react_agent.py:241`。\
**易错点：** Supervisor 和双路径在旧提交已有；这次补的是准确解释，不是声称新引入该架构。

### Q3｜为什么能缓存 Agent 模板，不能缓存请求状态？〔P0〕

**参考回答：** 模板描述可复用的执行结构；身份、输入、handler、writer 和日志上下文属于本轮执行。跨请求共享可变上下文会串号。ContextVar 隔离的是逻辑上下文绑定，`copy_context()` 并不深拷贝里面的可变对象；跨线程要显式传播，跨进程恢复要从可序列化数据重新装配活对象。

**代码事实：** Agent 模板缓存有 RLock；SkillContext/ToolContext 提供 set/reset。子 Agent 可能继承父 handler 引用，另有 `_IN_SUBAGENT` 标识防止事件混入父流。claimed 恢复先重建运行态，再绑定上下文，并在 finally 中清理。

**追问：有 ContextVar 就线程安全吗？** 不等价。多个上下文仍可能引用同一个 client、list 或 handler。要继续看对象所有权、可变性、线程安全和释放责任。

**证据：** `P:biz/aichat/skill/agent.py:423`、`:1368`、`:1450`；`P:biz/aichat/skill/middlewares/polling_bridge.py:9`、`:19`、`:56`。\
**验证思路：** 两个身份并发、子 Agent 嵌套、跨线程日志串联、执行后 ContextVar 重置，分别检查。

### Q4｜为什么使用专用 event loop？为什么超时不直接强制取消？〔P0〕

**参考回答：** 数据库异步连接等资源与 event loop 有绑定关系。专用 loop 避免跨 loop 误用，也隔离 HTTP 主 loop 与后台控制面。同步线程通过 `run_coroutine_threadsafe` 等待结果，异步入口通过 wrap_future 接入等待。超时只表示调用方不再等，不保证底层操作没有完成。

**代码事实：** loop bridge 对关键等待使用 shield，避免中断已经开始的控制面流程；同 thread_id 有本进程执行登记。业务 TaskExecutor、ADK executor 和工具执行池是不同层的池，容量不能混算。

**追问：shield 会造成资源泄漏吗？** 它保留了工作继续运行的可能，必须另有状态观测、生命周期托管和最终清理。它不是无限执行的许可，也不是幂等保证。同步阻塞代码仍能卡住专用 loop。

**证据：** `P:pl_agent/adaptive/loop_bridge.py:25`、`:45`、`:86`、`:103`、`:194`；`P:pl_agent/bootstrap.py:141`。\
**验证思路：** 调用方超时后底层成功、同任务重复进入、数据库连接 loop 不匹配、loop lag 和排队延迟。

### Q5｜恢复任务为什么不能重新走一遍 create？〔P0〕

**参考回答：** 恢复要延续原任务的等待条件、状态和执行身份。当前 native resume 先查控制面归属，再满足等待条件，由调度流程领取后续 execution；不是再生成一次普通请求。claimed 执行使用权威 prepared config 和 checkpoint_id，并在驱动图前恢复 handler/writer 等运行对象。

**代码事实：** 探测区分 `db_hit/db_miss/probe_failed`。native resume 调 `asatisfy_native`，handled 后不启动普通 create runner。驱动前预恢复很重要：如果恢复时已没有节点可跑，靠节点 hook 初始化 writer 就可能遗漏。

**追问：DB 探测失败等于没有任务吗？** 不等价。当前 probe_failed 会记录原因后走 legacy，这是一项现有降级选择，不能据此承诺 native 恢复必然成功。更强协议可考虑返回可重试的未知状态，取舍是可用性与一致性。

**证据：** `P:pl_agent/adaptive/task_mode.py:13`、`:88`、`:114`；`P:pl_agent/adaptive/control_plane.py:268`；`P:biz/aichat/skill/agent.py:1362`、`:1417`。\
**验证思路：** 已满足的重复回调、DB 超时、无节点可跑、恢复配置缺字段、旧执行仍存活。

### Q6｜取消 API 成功，是否保证执行已经停了？〔P0〕

**参考回答：** 需要区分控制面接受取消、信号投递、主运行收尾、外部副作用停止。当前接入先尝试取消控制面 execution，再向运行中的 handler 发信号。取消 API 的成功条件还包括已终态或已提交恢复清理，不能统一翻译成“所有资源已释放”。

**代码事实：** 本地 Event 取消等待 done 最多约 10 秒，但 `done.wait` 返回值没有参与 True 的判定；跨 worker 标记等待到期也可能返回 True。因此返回值更接近“找到并触发取消”，不是严格收尾确认。挂起任务的 recovering 也只说明后台清理恢复已提交。

**追问：如何改进？** 对外区分 requested/stopping/stopped/cleanup_failed，保留最终确认与查询入口。远程工具如果已经提交，只能核对结果或补偿；不能声称取消回滚。

**证据：** `P:pl_agent/agent/asservice/mcp/polling.py:976`、`:1037`；`P:biz/aichat/skill/polling/post_polling/pipeline.py:122`、`:153`、`:162`；`P:biz/aichat/skill/interrupt_recovery.py:88`。\
**验证边界：** 上述等待语义由源码确认；本轮未注入超时、未证明发生了线上事故。

### Q7｜为什么挂起、取消和迁移不能共用一个“完成”状态？〔P0〕

**参考回答：** 挂起意味着等待用户或外部条件；迁移意味着执行所有权转交；取消意味着不再继续正常任务。它们对 writer、浏览器会话、checkpoint 和指标的清理动作不同。逻辑任务可以跨多个 execution segment，task_id 用于串联任务，run_id 用于区分尝试，不能每次恢复都当成新成功请求。

**代码事实：** 结果分 COMPLETED/SUSPENDED/FAILED/CANCELLED，CANCELLED 还会查询是否 MIGRATED。挂起不发普通 done；挂起/接管保留相应资源。首轮与 claimed 执行复用收尾逻辑。新版加强了真实后台执行段、route/reason 和控制面元数据的观测。

**追问：能证明租约算法一定正确吗？** 本轮只核对本仓对 ADK 控制面的调用、参数与结果处理，没有审计外部依赖内部 CAS/租约实现。读取迁移状态失败也可能影响本地分类，不能过度承诺。

**证据：** `P:biz/aichat/skill/agent.py:752`、`:904`、`:1089`、`:2393`；`P:pl_agent/adaptive/failover.py:18`；`P:pl_agent/adaptive/control_plane.py:50`。

## 四、Checkpoint 与恢复：项目面试题

### Q8｜用了 Checkpoint，就能在任意位置宕机恢复吗？〔P0〕

**参考回答：** 要看保存的内容、触发点和后端。当前 BOSCheckpointSaver 继承 InMemorySaver，普通步骤写内存；根命名空间收到 `__interrupt__` 才持久化 thread 快照。挂起轮关闭输出流后再 repersist，把尾部产生的序号等更新进去。这主要服务特定挂起点的恢复，不能推导为每个工具完成都已持久保存。

**代码事实：** writer、handler 等运行对象不直接序列化，可恢复数据放到 ckpt 字段。BOS 配置或构造不满足时，工厂返回 None，业务回退 MemorySaver；这种情况下不能跨新进程恢复旧内存。

**追问：为什么不每个 token 都保存？** 它会增加序列化、存储和延迟成本；恢复点应与业务步骤及副作用边界对应。取舍必须明确最大可丢进度和可重放范围，而不只报“用了 LangGraph”。

**证据：** `P:biz/aichat/skill/checkpoint/bos_checkpointer.py:527`、`:560`、`:737`；`P:biz/aichat/skill/agent.py:2104`；`P:biz/aichat/skill/state.py:1431`。\
**失败推演：** 外部工具已完成、尚未形成可恢复持久状态时进程退出，不能从 Checkpointer 名称推断工具可安全重跑。

### Q9｜PUT 后 HEAD 成功，能证明这次快照写成功吗？〔P0〕

**参考回答：** 当前只检查对象 key 存在，没有比较本次 envelope 的版本或摘要。底层 put_object 会捕获异常；如果该 key 已有旧对象，本次覆盖失败后 HEAD 仍可能成功。所以“对象存在”与“本次新内容已确认写入”是两条不同证据。

**读取侧还有一层边界：** 上层 hydrate 区分确定不存在和临时错误，后者退避并保留重试；但底层 object_exists 对所有异常返回 False。网络错误若在底层被吞掉，上层可能把它当不存在并缓存确定性结论。要分析完整调用栈，不能只读上层注释。

**追问：如何增强？** 让不存在与读取失败返回不同结果；写入可绑定单调版本、内容摘要或服务端条件写契约，并核验本次版本。仅“重试 HEAD”仍不等于核验内容。删除失败同样要保留待处理或未知结果，不能宣称取消后永久无法恢复。

**证据：** `P:biz/aichat/skill/checkpoint/bos_checkpointer.py:231`、`:279`、`:353`、`:437`、`:594`；`P:pl_agent/library/common/bos.py:74`、`:200`、`:213`。\
**验证边界：** 这是静态失败推演，未做线上复现。现有观测测试使用成功 fake，不证明“旧对象存在但本次覆盖失败”已经被覆盖。

### Q10｜恢复时为什么不只恢复 messages？〔P0〕

**参考回答：** messages 是上下文的一部分，继续执行还需要技能缓存、浏览器绑定、运行身份和输出连接。当前恢复会重建 PollingHandler/stream writer，复制 ckpt 字段和技能搜索缓存，按需要还原浏览器 tab/ws_url；这些活对象不能直接靠序列化网络连接恢复。

**代码事实：** claimed 路径在图驱动前预恢复，避免“没有可运行节点，所以初始化 hook 不触发”。挂起轮不发普通 done、不按普通终态释放浏览器会话。恢复后是新连接继续旧逻辑任务。

**追问：图状态恢复了，为什么页面仍收不到？** 可能 writer 没有重建、资源身份不匹配、事件流恢复失败或终态提前发送。应把图执行、运行资源和传输三个层面分别定位。

**证据：** `P:biz/aichat/skill/agent.py:546`、`:893`、`:934`、`:1301`、`:1417`；`P:biz/aichat/skill/state.py:1431`。\
**验证思路：** 空待执行节点、浏览器登录后恢复、技能缓存缺失、恢复 writer 失败、重复回调，不只比较 messages 一致。

### Q11｜序号连续与 ACK 为什么仍不能证明用户看到完整内容？〔P0〕

**参考回答：** interrupt 之后还可能发尾部卡片，所以收尾需刷新最终 seq。新 writer 的 open_for_resume 跳过 already-sent 首包，从保存序号后继续。ACK 是执行服务与下游之间的传输确认，不是浏览器业务提交或 DOM 渲染回执。保存 writer 当前 seq 也不等于此前全部 seq 都已确认。

**代码事实：** `_seq_base` 排除上一轮编号，累计 ACK 集合跨同一 writer 的重连保留，避免误报丢包；收尾按当前区间计算 missing。BOS repersist 会刷新相关 namespace 的 skill blob，不应只看根节点。

**追问：浏览器的 checkpoint 是同一个吗？** 不是。前端 body 传“下一条序号”，G 的 XREAD 需要“上一条位置”；例如收到 10 后传 11，未提供 Last-Event-ID 时，网关用 10-0 起读；同时提供时 Header 优先。执行侧 open_for_resume、网关读取游标、客户端业务提交水位是不同状态。

**证据：** `P:biz/aichat/skill/checkpoint/bos_checkpointer.py:560`、`:617`；`P:pl_agent/servicer/pbrpc/agent_event/__init__.py:401`、`:592`、`:1524`、`:1661`、`:1802`；`F:packages/chat-stream/src/actions/request-actions.ts:338`；`G:model/service/agentapi/pump.go:537`。\
**失败推演：** 尾包未保存导致序号重叠；发送成功但 ACK 丢失；客户端解析后业务消费失败；Redis 裁剪超出恢复窗口。

### Q12｜Checkpoint、claim 和租约各解决什么？〔P0〕

**参考回答：** Checkpoint 回答恢复什么状态；claim/租约回答当前谁有资格继续执行和提交状态。当前接入层能看到 router 的领取/准备流程、权威 prepared config、pinned checkpoint_id，以及 run_id/lease_epoch 的使用。仅有一份恢复数据，无法阻止两个 worker 同时向外部系统写入。

**证据边界：** MySQLBackend 和控制面底层属于 ADK 依赖。本轮核对了本仓集成代码，没有逐行确认其 SQL 原子领取、续租或 CAS 实现，不能把依赖接口名字当成已证明的强一致性。

**追问：有 fencing token 就 exactly-once 吗？** 还需要资源接收方验证新旧代际。旧 worker 若继续调用不识别 fencing token 的外部工具，副作用仍可能重复；数据库拒绝旧状态写入不自动阻止远端写操作。

**证据：** `P:pl_agent/adaptive/control_plane.py:189`、`:268`；`P:pl_agent/adaptive/restart_policy.py:50`；`P:pl_agent/adaptive/supervisor_init.py:179`；`P:biz/aichat/skill/agent.py:1362`、`:1431`。\
**验证思路：** 领取竞争、租约过期后旧 worker 继续运行、恢复状态不一致和下游拒绝旧代际，应分层验收。

### Q13｜失败后从原始输入重跑，为什么可能比返回失败更危险？〔P0〕

**参考回答：** 重跑会重新产生已发生的输出或副作用。当前首包发送后会声明 `restart_from_input_unsafe`，因为前端可能已经有气泡；但该声明是异步提交，并不与发送首包构成原子事务。外部写操作也是一样：请求带 qid 不代表下游按 qid 去重。

**代码例子：** pl_enter 客户端明确区分重试条件：连接阶段超时或明确未投递的业务错误可有限重试；读超时、一般连接错误或服务端 500 等结果不明场景不盲目重放。原因不是“重试不好”，而是不能证明前次没有生效。

**追问：怎样处理未知结果？** 使用由业务意图稳定派生的 operation_id、下游去重与结果查询；本地保存执行凭据。对不支持查重和查询的外部系统，只能暴露未知、人工核对或设计补偿，不能凭 Checkpoint 承诺 exactly-once。

**证据：** `P:biz/aichat/skill/nodes/parse_request.py:1304`；`P:pl_agent/adaptive/restart_policy.py:1`、`:77`、`:116`；`P:biz/aichat/skill/clients/pl_enter.py:36`。\
**失败推演：** 首包发出但禁止重跑声明失败；外部提交成功但回执丢失；新建幂等键后重复发起同一用户意图。

## 五、工具调度与技能：项目面试题

### Q14｜工具已注册、模型可见、允许执行为什么要分开？〔P0〕

**参考回答：** 注册构造可用能力，模型可见决定本轮能选什么，执行准入决定当前主体是否真的可以调用。模板缓存不能固化请求级开关。当前 scheduler 常驻构造工具表，但执行时读开关；TODO 会按本轮状态从模型 tools 中移除；switch_model 除 Schema 外还会检查目标配置。

**边界：** 隐藏工具不等于授权。JSON Schema 约束也不等于资源归属或副作用许可。当前 scheduler 读取开关失败采用 fail-open，不能把它描述成所有工具统一严格拒绝。switch_model 的 available 为空也不能机械解释为一律禁止所有模型。

**追问：安全边界应放哪里？** 可信服务端在每次执行前验证主体、资源、参数和预算；高风险动作需要对应许可。提示词用于引导，不能代替执行准入。

**证据：** `P:biz/aichat/skill/nodes/react_agent.py:110`、`:125`、`:241`；`P:biz/aichat/skill/middlewares/skill_scheduler.py:123`、`:166`；`P:biz/aichat/skill/middlewares/todo.py:140`；`P:biz/aichat/skill/tools/model.py:54`。\
**旧口径纠正：** 不能用残留旧 ToolExecutor 的默认并发、Guard/RateLimit/Retry 描述这条 ADK 主链；旧类中的放行或 stub 也不是已有完整治理能力的证据。

### Q15｜middleware 顺序为什么会影响业务和前端正确性？〔P0〕

**参考回答：** wrapper 有外内层，after hook 可能逆序执行。外层提前 return 会跳过内层工具调用、事件与清理。当前 RSI guard 放在 scheduler 前，评估拒绝可以发生在创建调度客户端前；capture/action、TODO/Observe、清理/push gate/summarize 的顺序都有语义。

**最新变化：** 参数解析错误、取消等早退补齐工具 start/end，并以相同 tool_call_id 关联。仅给模型一个 error 文本不够，前端 pending preview 也要闭合，否则可能直到 finalize 才显示或永远等待。

**追问：怎么测试 middleware？** 按每条分支检查 handler 是否调用、产生了什么副作用、模型拿到什么、前端是否收到配对结束事件、资源是否释放。测试不要只断言返回字符串。

**证据：** `P:biz/aichat/skill/nodes/react_agent.py:245`、`:258`、`:283`；`P:biz/aichat/skill/middlewares/rsi_eval_guard.py:43`；`P:biz/aichat/skill/middlewares/skill_task.py:157`；`P:biz/aichat/skill/middlewares/skill_action.py:390`。\
**失败集：** 参数错误、开关拒绝、池满、人工中断、用户取消、超时、异常，分别看协议收口。

### Q16｜用户停止、工具超时、人工介入是不是同一种控制流？〔P0〕

**参考回答：** 不是。未执行的工具可直接拒绝；已执行工具遇用户取消时，当前实现 detach 放弃等待，不强杀工具。超时路径对本地 task 发 cancel 并报告 TimeoutError。人工介入则是挂起控制流，AdkToolInterrupt/GraphBubbleUp 必须向上透传，不能被包装成普通工具失败。

**代码事实：** 取消轮询约 0.2 秒；默认工具超时与 builtin_task、图创等工具有不同配置。`request_user_action` 使用 async 调用 interrupt，以保留运行上下文；恢复时入口可能重放，额外判断避免重复创建 openWorkspace 卡片。

**追问：task.cancel 能停掉同步线程吗？** 不能据此保证线程、远端调用或已受理副作用终止。超时分支也只是发出取消，不是外部事务回滚证明。UI 的“停止”等待和操作是否完成必须分别呈现。

**证据：** `P:biz/aichat/skill/middlewares/skill_action.py:107`、`:459`、`:531`、`:616`；`P:biz/aichat/skill/middlewares/constants.py:35`、`:58`；`P:biz/aichat/skill/tools/interrupt.py:17`。\
**可核对测试：** `P:tests/biz/aichat/skill/middlewares/test_skill_action_cancel.py:51` 明确包含主链路取消后工具继续执行的场景；本轮未运行。

### Q17｜子 Agent 的 12 线程池为什么不是完整预算？〔P0〕

**参考回答：** 子 Agent 的同步流读取可能阻塞 loop，因此它在线程池中建立独立事件循环，并复制必要上下文。12 是单进程子 Agent 池的容量，不是集群总并发、每用户配额或总 Token/费用上限。多 worker、多副本会放大总数。

**代码事实：** 进入前 `try_reserve()` 原子预留，池满拒绝，避免“全部先通过后无界排队”。子 Agent 工具列表不继承 builtin_task，以阻止递归派生；跨 loop 前清理父 Supervisor migration context，避免复用父 loop 的异步连接。

**值得追问的边界：** named 子 Agent 的 max_iterations 传入构造器；匿名配置虽然有该字段，匿名构造路径没有显式传入，因此不能承诺两者预算一致。也不能仅凭池容量保证所有 detach 后的工作与预算都已经统一收口。

**建议：** 再设计请求级时长、Token、费用、工具次数和租户配额，并核对子任务预算如何传递、扣减和归还。

**证据：** `P:biz/aichat/skill/subagent/task_tool.py:62`、`:305`、`:323`、`:474`、`:508`、`:583`；`P:biz/aichat/skill/middlewares/skill_task.py:128`；`P:pl_agent/library/common/tracked_pool.py:76`；`P:biz/aichat/skill/middlewares/constants.py:318`。

### Q18｜Resource Finder 的最新约束有哪些？〔P1〕

**参考回答：** 当前强调明确子任务目标和交付边界：query 自包含搜索情况、缺口与验收要求，description 用于用户展示。每请求只启动一次 Resource Finder；它与其他子任务互斥，同批混发会拒绝。child 的 final/done/tail 不透传成父任务终态。

**与旧版不同：** 已移除 resource_assessment 自检门禁，以及 Resource Finder 结束后禁止主 Agent 继续搜索的旧硬限制。不能再把这些写成当前设计。对返回结构中的 manifest_path/report_path，以及 files 列表中的 path/local_path 做归一化与 session workspace 边界检查；URL、sandbox:/ 和 data: 类值另行放行，并非所有输出都受统一文件路径约束。结果状态可表达 partial/failed/user_action_required。

**边界：** 提示词建议使用 named，不代表代码禁止 anonymous。结果结构解析失败时仍存在把文本包装成 success summary 的路径，不能说成功状态已严格证明产物通过验收。

**追问：怎么让成功更可信？** 为产物建立可检查契约，区分声明成功、结构可解析、文件存在与业务验收通过；失败/部分成功应保留来源和缺口。

**证据：** `P:biz/aichat/skill/subagent/resource_finder.py:48`、`:111`、`:132`；`P:biz/aichat/skill/subagent/task_tool.py:129`、`:345`、`:392`、`:562`；`P:biz/aichat/skill/middlewares/skill_task.py:120`。

### Q19｜加载 Skill 与防 Prompt Injection 有什么区别？〔P0〕

**参考回答：** Skill 加载把能力说明或流程加入上下文；它不自动证明内容可信。当前 load_skill 一次加载一个 name，支持名称映射、去重与已加载集合保存。注入的 skill_processor 是去行号处理，不是安全扫描器。

**最新能力与边界：** RSI Skill ZIP 有文件数、解压大小、路径、链接与 SKILL.md 格式约束。这能限制文件打包层面的危险输入，但内容仍可能诱导模型越权。前端用户选择的 skillId、UTF-16 区间也只是输入契约，不能代替后端执行授权。

**追问：如何防注入？** 把 Skill、网页和工具结果当低信任数据；通过可信身份、工具范围、参数和资源限制控制实际动作。对高风险副作用另设许可与幂等。不要把“提示词要求忽略恶意指令”当作隔离机制。

**证据：** `P:biz/aichat/skill/tools/skill.py:164`；`P:biz/aichat/skill/agent_resources.py:581`；`P:biz/aichat/skill/rsi/skill_pin.py:21`；`F:packages/chat-search/src/client/component/chat-skill-panel/query.ts:28`。

### Q20｜最新 RSI 实现了什么？是否已经是自动自进化系统？〔P0〕

**参考回答：** 当前落地的是执行侧版本化实验输入与环境还原。支持 prompt.full、prompt.section、skill.pin 和 model.config；先校验规则与产物，再应用覆盖和 replay。模型配置使用请求副本；RSI runtime 不进 Checkpoint，不能把环境 replay 当成挂起恢复。

**代码事实：** 校验拒绝 cron、热点与 resume 的 RSI 请求；运行 guard 另拒绝调度器/人工介入等动作。环境还原在目标 session 锁下检查该 session 尚无历史记录，再重放历史工具/上传记录，并按策略复制历史；strict 失败时不复制历史。在途调用超出宽限时会保留恢复锁直到 TTL，取消 token 主要阻止后续步骤。

**追问：距离完整评测闭环还差什么？** 数据集、可靠评分、失败分类、成本/延迟统计、版本晋升与线上回退仍需要各自证据。仅凭最新 commit 提到 RSI，不能宣称已经实现自动优化和线上质量提升。

**设计建议：** 对同一任务固定输入、候选版本、模型配置与预算，比较业务成功、产物正确性、权限失败、成本和延迟；既保留成功样本，也保留失败/取消/未知分母。环境 replay 可能重新产生操作，不能等价于“无副作用回放”。

**证据：** `P:biz/aichat/skill/rsi/protocol.py:39`；`P:biz/aichat/skill/rsi/rules.py:103`；`P:biz/aichat/skill/rsi/stage.py:178`、`:236`、`:247`；`P:biz/aichat/skill/rsi/runtime.py:1`、`:77`；`P:biz/aichat/skill/rsi/checker.py:196`；`P:biz/aichat/skill/middlewares/rsi_eval_guard.py:27`。

## 六、相关知识：从项目问题回到通用原理

### K1｜Agent、ReAct、Tool Calling、MCP 与 Workflow

**是什么：** Tool Calling 是模型提出结构化工具请求的交互形式；ReAct 组织“决策—行动—观察”的循环；Workflow 明确任务步骤与依赖；MCP 提供能力接入协议；Agent Runtime 承担实际执行、状态与资源生命周期。

**为什么需要区分：** 会接工具不等于拥有可靠 Agent；换一个接入协议也不会自动解决授权、限流、失败恢复和副作用。

**怎样使用：** 简单且固定的业务优先显式流程；需要根据观察动态选择行动时使用 Agent。无论哪一种，都要在执行端建立参数、权限、预算和结果契约。

**例子：** “查库存→创建订单”可以是两步固定流程；模型可以帮助理解购买意图，但订单创建的幂等、库存事务与权限不能交给 Prompt 决定。工作项目对应 Q2/Q14/Q19；不能用这些工作实现证明学习项目已经具备同样能力。

### K2｜Checkpoint、Memory、幂等与 exactly-once

**是什么：** Checkpoint 保存某次执行的可恢复状态；跨任务 Memory 保存长期信息；幂等让同一操作意图重复执行仍保持同一业务效果。它们解决不同问题。LangGraph 官方也将线程级 Checkpointer 与跨线程 Store 分开，见[持久化说明](https://docs.langchain.com/oss/python/langgraph/persistence)（核对：2026-10-22；框架通用说明不代表本项目安装版本和自定义实现完全一致）。

**为什么需要：** 进程可能在“外部操作成功”和“本地状态登记成功”之间退出。恢复本地状态后重放操作，就可能造成重复副作用。

**怎样使用：** 定义稳定 operation_id；让下游按该键查重并保存结果；本地执行记录区分未发送、执行中、已确认、失败和未知。同键参数不同应拒绝为冲突，不能把“重复”一律返回成功。

**例子：** 建单成功后网络断开，重试应先按原 operation_id 查结果；生成新 ID 再创建不是幂等重试。把 event seq、tool_call_id、业务 operation_id 混成一个字段也不自动获得端到端 exactly-once。

**对应：** Q8/Q9/Q12/Q13。Checkpoint 可帮助定位恢复点，但不能独自证明外部副作用只发生一次。

### K3｜async、线程池、ContextVar、取消与 shield

**是什么：** async 适合在等待期间让出执行权；线程池可承载阻塞调用；ContextVar 传播逻辑上下文；取消是一种协作信号。它们都不等于操作系统级隔离。

**为什么需要：** 直接在 event loop 执行阻塞 I/O，会拖慢同 loop 上的其他任务。把它移入线程后，停止 await 仍可能留下正在执行的工作。

**怎样使用：** 异步客户端处理异步 I/O；同步调用通过合适 executor/to_thread 下沉；按所有权回收资源。TaskGroup 可管理相关子协程的失败与收尾；捕获取消后完成清理，通常继续传播。shield 会保护被等待工作不受该等待者取消影响，因此调用方超时后要查询事实，不能假设操作没发生。参见 [Python 3.12 协程与任务](https://docs.python.org/3.12/library/asyncio-task.html)（核对：2026-10-22）。

**例子：** 工具线程还在写文件时，主协程取消，不能立即删除该线程仍使用的目录，也不能重新派发同一次写入。copy_context 传播的是绑定，不能当作资源对象的深拷贝或跨进程持久化。

**对应：** Q3/Q4/Q6/Q16/Q17。设计并发预算时分别看任务线程池、子 Agent 池、数据库连接、模型并发和集群副本。

### K4｜Redis Stream、SSE、ACK 与背压

**是什么：** Redis Stream 提供按 ID 保存和读取事件的能力；SSE 是向客户端传事件的 HTTP 形式。XREAD 读取严格大于给定 ID 的记录，官方定义见 [XREAD](https://redis.io/docs/latest/commands/xread/)（核对：2026-10-22；不使用最新文档新增参数推定本地 Redis 版本）。

**为什么需要：** 后台任务可能长于浏览器连接，事件存储让订阅断开后可以继续读取，而不必重新执行任务。

**怎样使用：** 明确游标表示“上一条”还是“下一条”、事件保留窗口和坏包处理；给慢消费者定义恢复失败或快照重建方案。每个订阅者要完整接收同一任务流时，用独立游标；消费者组分工消费是另一种需求。

**项目例子：** G 的写侧先 XADD 再 ACK，但重复 ID 错误并没有自动核对旧 payload 后补成功确认；读侧坏包会跳过并推进位置。Redis 接受写入不证明配置满足零丢失持久化，ACK 也不是用户看到正文。

网关 io.Pipe 的写阻塞可以减慢当前连接的读取，却不能自动暂停独立 Agent 向 Redis 生产。容量估算需看任务并发 × 事件速率 × 平均字节 × 保留时长，加上存储开销，并考虑条数裁剪；DOM ack、网关管道背压和上游模型预算是三层控制。

**证据：** `G:model/service/streaming/handler.go:238`、`:289`；`G:model/service/agentapi/pump.go:336`、`:377`、`:537`；`G:model/service/agentapi/module.go:381`。对应 Q11。

### K5｜数据库事务、索引、outbox 与跨系统一致性

**是什么：** 事务保证其覆盖的数据库操作共同提交或回滚；索引影响查询与加锁成本；outbox 在本地事务内保存待投递意图，再由后台投递。outbox 消费仍需要去重。

**为什么需要：** 会话删除可能与回答追加竞争；数据库成功后，Redis 通知可能失败。把两次 SQL 放进一个事务，不会让外部模型或 Redis 自动参与同一原子提交。

**怎样使用：** 先明确权威状态与不变量，再让读写两方共同遵守锁、版本或状态条件。模型调用尽量在短事务之外，回来后重新核验状态与权限。按真实查询形状、DDL 和执行计划选择索引，不根据 DAO 方法名猜测线上索引。

**工作证据例子：** U 的历史查询包含 uid/session_id/message_id；删除 topic 与 record 使用同一数据库事务。这可以说明身份条件与提交边界，但不能证明已防止所有删除后追加，也不能证明 Redis 同步删除或任务已取消。

**设计追问：** 提交成功但 HTTP 响应丢失，客户端应凭业务键查询，不能把超时视为回滚。添加 outbox 可以减少“DB 已提交但通知没发”的丢通知窗口，却仍需处理重复投递和最终状态核对。

**证据：** `U:model/dao/chat_record.go:77`；`U:model/dao/chat_topic.go:124`、`:135`；`U:model/dao/tx_base.go:42`。本段是工作 DAO 代码与设计知识；学习项目的 PostgreSQL 实践必须单独提供证据。

### K6｜评测如何证明 Agent 真的变好了？

**是什么：** 评测是固定任务、版本、预算和判据后比较表现；环境 replay 是构建可比实验的一环，不是评分本身。

**为什么需要：** 回答更流畅、工具调用更多或某次执行成功，都不能证明整体质量、成本或安全性改善。

**怎样使用：** 先定义业务目标，保留成功、失败、取消、未知、无答案的不同分母；关键副作用由文件/数据库/业务事实断言，模型评审辅助表达与语义质量。分别统计首内容、总耗时、费用和失败类别；调参集与留出集分开，避免看完测试再修改规则。

**例子：** 新 Skill 让回答更完整，却多执行一次危险写工具，应在安全失败指标中被识别；不能只因文本质量分上升就晋升。RSI 提供候选输入与环境还原能力，晋升阈值、回滚和真实收益需要额外实现及证据。

**对应：** Q20。个人学习项目历史上的检索评测只能说明当时样例与方法，不能作为工作产品收益数字。

### K7｜可观测为什么也要分状态与边界？

**是什么：** trace 串请求和执行段，指标用于聚合，日志保留具体事件。逻辑任务可能经过多个 run/segment；一次 HTTP 成功不是业务成功指标。

**怎样使用：** 分别记录接入、排队、模型等待、工具执行、存储与最终交付；缺失时间点记 unknown，不能补零。task_id 可用于日志关联，但不宜直接做无限基数的指标标签。观测失败通常应隔离于业务，但不能吞掉业务错误后记成功。

**代码例子：** 最新 custom span 提供独立的 payload 快照与错误记录；Checkpoint 的 raw_str/get_object 内容还经过 payload selection 裁剪。不能看到调用方传 envelope，就直接宣称完整快照已经泄漏；也不能仅有裁剪函数就宣称所有数据均安全，仍要核对最终出口与配置。

**证据：** `P:pl_agent/observability/custom.py:113`、`:174`；`P:pl_agent/observability/payload_selection.py:186`、`:215`。对应 Q7/Q9/Q15。

## 七、建议准备的失败场景与现有测试入口

下表是面试准备与后续验证计划。本轮只阅读源码与测试内容，没有执行这些用例，也没有访问生产环境。测试名中的 EndToEnd 不自动证明真实跨服务端到端测试。

| 场景 | 应检查的不变量 | 当前可查证据／待验证边界 |
| --- | --- | --- |
| 创建成功，执行尚未开始 | 接受与结果分离，不能提前显示业务成功 | polling.py 的后台提交路径；需另测排队与响应丢失 |
| 恢复 DB 探测失败 | unknown 与不存在分开记录 | adaptive/task_mode.py；当前 fallback 的真实恢复效果待联调 |
| 用户取消，工具仍运行 | 停止等待不等于回滚 | `tests/biz/aichat/skill/middlewares/test_skill_action_cancel.py` |
| 取消等 done 超时 | API True 不冒充已释放全部资源 | post_polling/pipeline.py；本轮未复现 |
| 同 key 旧快照存在，新 PUT 失败 | 存在性不冒充新版本确认 | Checkpointer+BOSClient 静态推演；需专门故障注入 |
| HEAD 网络异常 | 读取失败不应被缓存为不存在 | BOSClient 的 False 返回与 hydrate 分类；需专门故障注入 |
| 挂起后仍发尾包 | 下一轮序号不回退、不重发首包 | `tests/biz/aichat/skill/test_bos_repersist.py`、`test_repersist_thread_id.py` |
| 新 worker 恢复 | 图状态与业务运行时都可用 | `tests/biz/aichat/skill/test_state_checkpoint.py`、`test_bos_live_ctx_isolation.py`；真实跨 worker 浏览器链路未验 |
| ACK 跨重连 | 保留累计 ACK，排除前轮 seq_base | `tests/servicer/test_agent_event_resume.py`、`test_agent_event_close_missing.py` |
| 业务 seq=11 从 Redis 读取 | 起点是 10-0，不跳过 11 | G 的 `model/service/agentapi/timing_test.go`；其中函数级组合测试不等于网络联合验收 |
| claim 后旧 worker 继续 | 状态写入与外部副作用分别约束 | 本仓只有集成证据；底层 ADK 租约/CAS 和下游 fencing 待核对 |
| 工具参数错误／外层拒绝 | start/end 配对，模型与前端都收口 | skill_action.py、skill_task.py；按每条早退验证 |
| 子 Agent 池满 | 准入预留原子、容量不超进程边界 | tracked_pool.py 与 SkillTaskMiddleware；集群配额未由此证明 |
| RSI 产物非法／还原超时 | 不把部分准备当成功，保留在途状态 | `tests/biz/aichat/skill/rsi/test_checker_rules.py`、`test_artifacts.py`、`test_replay_stage.py` |
| 观测代码异常 | 观测故障与业务故障分开 | `tests/biz/aichat/skill/test_bos_checkpoint_observability.py`、`tests/observability/test_claimed_execution_segment.py` |

## 八、面试前如何取舍与补证据

### 优先复习

- 第一轮：Q1/Q2/Q5/Q8/Q11/Q14，讲清完整链路和职责。
- 第二轮：Q6/Q9/Q12/Q13/Q16/Q17，讲清失败、未知结果和分布式边界。
- 第三轮：Q15/Q18/Q19/Q20，讲清工具协议、权限与评测；用 K1～K7 补原理。

这只是复习建议，不是公开面试频率统计，也不代表候选人已经掌握。

### 个人证据卡

每个准备讲的难点只需补齐下面几项，不编造职责或指标：

| 项目 | 待本人填写 |
| --- | --- |
| 来源 | 工作亲自交付／工作协作／源码复盘／独立学习 |
| 我的动作 | 具体设计、实现、排查或协作行为 |
| 问题与取舍 | 为什么选该方案，放弃了什么 |
| 证据 | 提交、需求、日志、测试或实际演示 |
| 结果 | 真实可核验结果；没有指标则说明验证方法 |
| 边界 | 当前不能保证什么、下一步如何增强 |

学习项目与工作项目分开举证。本次恢复没有重新运行学习项目代码或测试；可以引用既有题目中的验证记录及其边界，但不能把文档恢复写成“当前仓库已重新运行通过”，也不能把教练实现改称本人独立掌握。

### 适合的 90 秒收口

我关注的不只是模型有没有返回文本，而是请求接受、运行状态、工具副作用和最终交付是否一致。沿 ChatSearch 的工作链路，我能说明前端游标如何与 Go 网关对齐，也能解释 Python Agent 的创建与恢复归路、挂起状态持久化、工具准入和取消边界。涉及后端团队的实现，我会明确说明是协作理解与源码复盘；自己的后端实践则放在独立学习项目中提供证据。对于更强的一致性，我会先定义操作身份、状态与未知结果，再考虑幂等、租约和补偿，而不是只列框架名称。

## 九、本轮核对结果与限制

- 已读取 `77d5befe` 的业务实现，并与 `11b23042` 对比；修订执行、Checkpoint 和工具调度的事实口径。
- 已补充 20 道项目题、7 个知识专题、失败场景矩阵与可复查源码位置。
- 区分了既有能力、本次实际变化、源码失败推演和设计建议；不从“代码存在”推导个人贡献、上线状态或质量收益。
- 未运行服务、业务测试、真实 BOS/Redis/数据库调用、浏览器联合验收或生产压测。引用的测试表示“源码存在并可查”，不是本轮通过报告。
- 未审计外部 ADK 依赖的全部实现；特别是底层 claim/CAS、租约和完整任务恢复保证，不能仅由本仓集成调用证明。
- 2026-10-23 将恢复文档归档至 `interview-questions/projects/` 并恢复分类题库与导航；工作业务代码和学习项目业务代码均未修改，旧测试结果不作为本次验收证据。
