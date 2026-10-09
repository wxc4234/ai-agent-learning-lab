# 单批代码上下文协议

[构建服务](../apps/api/app/services/workspace/files/code_context.py)提供 `build_code_context(result: CodeVectorSearchResult, *, budget: CodeContextBudget | None = None) -> CodeContextPackage`。可信宿主显式选择[单批召回快照](code-vector-storage.md#指定批次精确召回)，服务只做纯内存校验、去重和选择，没有数据库事务、文件读取或模型请求。另有[候选选择→查询上下文内部组合](#授权候选选择与查询上下文内部组合)、下述显式查询→授权召回→构建组合、本地HTTP入口、同源BFF及[内部查询校验](#内部查询校验与产品边界)；已有[内部工具定义](#代码检索工具契约与内部适配)，已由[请求级注册与发送复核](#代码检索请求级注册与发送复核)接入本地流式Agent；没有混合排序或Rerank。

## 输入与来源

- 只接受指定的召回DTO；JSON序列化后最多2 MiB，再以严格模型解析，隔离嵌套字典/数组并拒绝额外字段。复用存储的文件/分块校验，检查规范相对路径、来源摘要一致、正文摘要、chunk ID、半开坐标、分片和正文范围。
- 批次1～20片，Top K 1～20，维度1～4096；批次、可召回、零向量排除、Top K省略和返回数量须一致。rank从1连续递增，距离有限且在0～2内、按非递减顺序排列；维度及空间声明须与metadata一致。
- 全部候选先通过校验再选择。预算外的坏片段、未知距离、来源异常或冲突重复都整次拒绝，不能用裁剪隐藏尾部失败。同一chunk ID只有内容和距离一致才可去重；相同正文出现在不同文件/位置时仍保留各自来源。
- 保留原始文件元数据、策略、模型声明、用量、truncated与原因。用量两项均未知或均为已报告非负整数，total不小于prompt；未知保持None，已报告零保留0。召回只返回部分定义分片时，不要求把未返回的其他片补齐。

校验只证明快照内部声明一致，不证明真实数据库查询、供应商身份或当前磁盘版本，也不重建当前权限。文件摘要只是与快照来源字段比较，分块正文摘要及ID则重新计算；旧快照/摘要/空间ID均不是后续读取或发送许可。正文继续标记`untrusted_project_content`，JSON和标签不构成提示注入防御。

## 预算与选择

预算使用严格冻结的`CodeContextBudget`构造：

| 字段 | 默认 | 范围与计量 |
|---|---|---|
| `max_chunks` | 5 | 严格整数1～20，选中完整片段数 |
| `max_chars` | 12000 | 严格整数1～80000，最终context_text的Python Unicode字符数 |
| `max_bytes` | 24000 | 严格整数1～160000，最终context_text的UTF-8字节数 |

`context_text`是使用`ensure_ascii=False`、紧凑分隔符和禁止非有限数值生成的JSON。预算包含键名、来源引用、距离、正文转义和覆盖声明，不只计算原代码长度；字符/字节不是精确Token数。

1. 先构造空片段JSON；来源与覆盖声明也占预算，放不下时返回`code_context_budget_too_small`，不删除声明或返回伪空成功。
2. 按原召回顺序处理，相同chunk ID记录`duplicate_chunk`并保留第一次。首次候选即使因预算未选中，后续重复也不重试；去重优先于片段数量限制。
3. 已达到片段数时记录`chunk_budget`。否则试加完整片段并重新渲染，按实际文本分别检查`character_budget`与`byte_budget`，同时超出时保留两个原因。
4. 放不下时继续尝试后面的完整小片段，不break、不切正文、不改坐标或摘要、不重新排序。此贪心策略保证稳定与可解释，不保证最大化预算利用或语义收益。

输入2 MiB限制发生在序列化后；当前解析和选择都在应用进程内，没有硬内存/CPU隔离。最多20个候选限制了重渲染次数。

## 输出与覆盖

`CodeContextPackage`包含批次/空间/维度、输入与选中数量、context_text及其实际字符/字节数、预算、选中完整片段、每个省略候选的原rank/chunk ID/原因、source_metadata与recall_summary。source为`bounded_code_context`；距离仍不是置信度。

JSON明确scope为`selected_chunks_from_one_batch`。原`truncated/incomplete_reasons`描述生成覆盖，`recall_omitted_by_top_k`描述召回选择，`builder_omitted_hits`描述本轮上下文选择；互不替代，也不代表整个项目覆盖。全零批次可产生明确的无片段上下文，零向量排除数量保留在审计摘要。

审计字段与context_text分开，不自动进入模型提示；当前预算不涵盖整个返回DTO、系统指令、历史或其他工具结果。本地search_code请求通过下述发送守卫对完整messages/tools另行计量并确认当前发送许可；直接构建DTO仍不授予许可。DTO和预算字段冻结，但审计嵌套字典仍可由调用方修改；它们是与输入及其他构建结果无别名的副本，修改不会改写已生成的context_text。

## 授权查询上下文串联

[组合服务](../apps/api/app/services/workspace/files/code_query_context.py)提供 `build_code_query_context(query, *, user_id, workspace_id, task_id, batch_id, config, response_model, top_k=5, budget=None, transport=None) -> CodeQueryContextResult`。可信宿主明确选择身份/Workspace/Task、发送的查询文本、目标批次及预期模型空间，显式传独立Embedding配置；预算省略时使用上述默认值，transport仅供可信测试注入。

1. 预算对象必须为CodeContextBudget，并在查询/数据库/HTTP前从三个字段重新构造严格预算，阻止model_copy/model_construct绕过范围约束；不直接序列化非法字段，避免警告反射输入。使用新预算对象隔离调用方，随后预检查询文本并捕获实际UTF-8摘要。
2. 复用[授权查询召回串联](code-vector-storage.md#授权查询与召回串联)，先短事务核对当前归属/绑定/指定批次，Session关闭后请求模型，HTTP收尾后重新授权召回。组合层不增加外层事务、文件读取、重试或结果缓存。
3. 再核对结果类型、本次查询摘要、请求/报告模型、一次请求来源、用量、目标批次/空间/维度/Top K及Workspace/Task/模型声明。未知对象或错配声明返回code_context_query_result_invalid，不进入构建。
4. 两个短事务与HTTP全部退出后，调用纯内存Context Builder。其来源/预算失败整次传播，既不发布部分结果，也不放宽预算、重试模型或回退旧包；成功后才返回组合DTO。

返回CodeQueryContextResult：顶层query_sha256、requested_model、response_model、prompt_tokens、total_tokens和request_count描述本次查询请求；query_source=query_embedding，source=code_query_context，context为独立上下文包。原批次代码生成用量与请求次数保留在context.source_metadata，不与本轮查询相加；缺失用量仍为None，已报告零仍为0。原覆盖、召回省略和构建省略各自保持，结果不再附带全部原召回正文、查询正文或向量。

前置预算检查只证明类型与范围合法，不能在实际召回前确认来源声明和片段是否放得下。合法但过小的预算、存储来源异常或其他构建失败可能发生在一次成功模型请求之后；拒绝结果或数据库回滚不能撤销供应商已处理的请求或可能用量。生成失败、超时、外部取消与事务退出失败沿用上游契约，不构造空成功。

组合成功只证明本次内部链路，快照仍不授予后续读取或最终提示发送权。没有自动扫描、索引重建、最终模型提示发送或精确Token计量；HTTP超时不约束同步短数据库操作和纯内存构建。

| 错误码 | 边界 |
|---|---|
| `code_context_budget_invalid` | 非预算模型对象；组合入口还重新校验模型字段范围 |
| `code_context_query_result_invalid` | 组合入口收到未知/错配查询结果、目标或用量声明 |
| `code_context_snapshot_invalid` | 未知输入、序列化/严格解析、来源、计数、距离或重复冲突失败 |
| `code_context_snapshot_too_large` | 实际输入JSON超过解析前规模预算 |
| `code_context_budget_too_small` | 空片段来源声明也无法容纳 |

内部业务错误固定，不反射正文、路径或异常细节；预算模型构造的非法字段由Pydantic ValidationError拒绝。HTTP映射见下节。

## 授权候选选择与查询上下文内部组合

[build_code_retrieval_context](../apps/api/app/services/workspace/files/code_retrieval_context.py)接受可信宿主传入的query、user_id、workspace_id、task_id、独立EmbeddingConfig、top_k=5、budget=None及测试用transport=None。批次与报告版本由[兼容选择服务](code-vector-storage.md#授权兼容代码批次选择)确定，不接受模型或浏览器指定内部目标。没有HTTP/PC/Agent注册、索引生成、自动刷新、其他空间回退或最终提示发送。

先重新校验预算、查询、严格整数Top K 1～20及独立配置，再读取授权窗口；参数错误不能被空窗口掩盖。摘要服务事务退出后检查选择结果的类型/目标与状态，选中项还须符合当前请求模型、维度与完整空间；装配错配为code_context_selection_invalid。未找到保留selection的candidate_count/has_more/limit，不调用查询服务或创建HTTP客户端。最多检查原20批窗口，更旧批次不补查。

选中时显式传递batch_id/response_model及同一校验后配置、原始查询和预算，复用已有build_code_query_context。完整成功路径有三个独立短事务：摘要选择→发送前授权→HTTP退出后重新授权召回；模型等待无连接/归属锁，构建发生在全部资源退出后。组合层不增加外层Session，不缓存许可，不重试或重新选择。选择后、HTTP等待期间撤销归属/改走再改回/删除批次都可拒绝；实际报告版本变化、坏响应、合法但过小预算与事务退出失败均传播，不能改成未找到或空成功。

返回CodeRetrievalContextResult，source=code_retrieval_context。status=not_found_in_window时query_context=None；status=context_ready时query_context是完整CodeQueryContextResult，即使召回或构建后没有片段也保持成功。selection只保留窗口与候选事实；查询用量、历史代码用量、原覆盖、召回和构建省略继续由query_context分开维护，不相加或重复拼装正文。未找到不伪造查询用量为0。

取消在入口、选择后及查询返回后复核；内部依赖吞取消后仍拒绝迟到结果。HTTP等待超时/取消关闭本轮客户端，不承诺同步数据库中断、整个组合硬超时或供应商未计费。成功结果仍是快照，不授予后续文件读取或最终模型发送许可；最终提示完整预算、真实供应商/语义效果、Agent调度和浏览器联合未验收。

[23项预检/阶段测试](../apps/api/tests/workspace/files/test_code_retrieval_context_validation.py)与[21项真实PostgreSQL/受控HTTPX测试](../apps/api/tests/workspace/files/test_code_retrieval_context.py)通过，共44项新增：非法查询/预算/配置/Top K在选择前拒绝、参数复制与显式传递、选择错配/异常不重试、吞取消拒绝；空/错空间/窗口外兼容不建客户端，成功空上下文与未知/零/已报告用量区分，原覆盖/构建省略保留，只读SQL、三个事务退出与HTTP资源关闭次序，选择后/请求中真实提交撤销/绑定往返/删除，版本/HTTP/后置预算失败及真实超时/取消。使用根随机测试库/私有schema，真实提交并自动清理，不访问开发业务表或真实供应商。

核心及配套由教练按本课明确授权完成，不代表学习者独立掌握。既有选择/查询/构建/仓储未修改，不扩跑旧领域、前端或账号专项。三个新增Python文件Ruff/Pyright通过。复跑：在apps/api执行`../../.venv/bin/python -m pytest tests/workspace/files/test_code_retrieval_context.py tests/workspace/files/test_code_retrieval_context_validation.py -q --tb=short -W error`。

## 代码检索工具契约与内部适配

[make_search_code_definition](../apps/api/app/tools/search_code.py)仅创建search_code异步ToolDefinition，不修改全局/请求注册表或聊天链路。唯一参数模型SearchCodeArguments生成模型Schema，只允许query：非空白、无NUL、有效UTF-8，最多2000字符/4096字节，保留原始空白。额外身份、路径、批次、配置或预算一律拒绝；直接调用执行器也重新校验。

宿主在工厂显式注入独立EmbeddingConfig、Top K和CodeContextBudget，重新验证并复制配置/预算；transport只供可信测试。ToolExecutionContext由服务端提供当前身份/项目/任务，不能从模型参数取值，也不是永久许可。执行复用选择→查询上下文组合，继续当前授权、事务外模型等待和后置授权，无额外Session、刷新、缓存或重试。定义声明75秒Runtime等待预算，当前由下述请求级绑定接入既有调度；等待取消不保证同步SQL立即停止。

投影先限制内部序列化快照为2 MiB，再核对本次目标、查询摘要、选择状态、模型空间、来源/分块、覆盖及召回计数。模型Observation只含source=authorized_code_search、content_trust=untrusted_project_content、scope、status、search_window、coverage、matches。matches只含relative_path、symbol、start_line/start_column、end_line/end_column与text；坐标从1开始、结束位置不包含。批次/空间ID、维度、距离、哈希、向量、配置、宿主根路径、原查询及用量审计不直接投影；查询用量仍由底层结果维护，本课不接AgentRun计费。

not_found_in_window时coverage=None、matches为空，保留窗口has_more且不创建模型客户端；context_ready允许零片段。coverage分别保留原快照截断/原因、Top K省略、零向量排除和构建省略。完整JSON按实际UTF-8限64 KiB，精确边界接受、超限整次失败，不剪断JSON或丢弃来源。字节限量发生在序列化之后，不是独立硬CPU/内存隔离；正文/路径仍是不可信项目资料，标签和JSON不构成提示注入防御。

错误沿用SafeToolExecutionError白名单：非法参数code_search_request_rejected；归属拒绝workspace_not_accessible；未绑定workspace_directory_unbound；模型超时code_search_timeout；报告空间变化code_search_space_changed；来源声明放不下code_search_budget_exceeded；完整输出超限code_search_result_too_large；其余未知业务/模型/投影错误code_search_unavailable。未知code和原异常不反射。取消继续传播，吞取消的迟到结果再次拒绝。最终tool_call_id/tool_name错误封装由既有Runtime负责，本课不新增调度器；工具失败不是HTTP请求体422，也不能当成空匹配。

[50项纯契约/投影测试](../apps/api/tests/tools/test_search_code_tool.py)与[8项真实PG/受控HTTPX集成](../apps/api/tests/tools/test_search_code_integration.py)通过：Schema唯一且拒绝额外权限参数、定义不注册/需上下文/宿主参数注入、两种空结果与可读来源投影、坏目标/摘要/空间/版本/路径/正文/计数/字段拒绝、固定错误脱敏/不重试、取消传播/吞取消拒绝、完整UTF-8精确上限、内部快照超限；真实组合验证空窗口不发送、零片段/原覆盖/构建省略、无批次写入、当前归属与HTTP等待撤销、版本/HTTP/预算失败。真实数据库仍为根夹具随机库/私有schema并自动清理，无开发业务表或真实供应商。

核心与配套由教练按本课明确授权完成；4个受影响Python文件Ruff/Pyright通过。只在errors白名单新增固定分类，既有错误逻辑/服务/注册/前端未改，不扩跑旧领域或账号专项。本段证据只针对内部适配；请求调度及发送授权/字节预算见下节，真实供应商语义、浏览器或Windows仍未验收。复跑：在apps/api执行`../../.venv/bin/python -m pytest tests/tools/test_search_code_tool.py tests/tools/test_search_code_integration.py -q --tb=short -W error`。

## 代码检索请求级注册与发送复核

[请求绑定](../apps/api/app/services/runtime/agent/code_search_binding.py)由ChatExecution.bind_code_search_tool提供，仅在APP_MODE=local的/chat/stream调用。每次请求延迟加载独立Embedding配置；未配置或配置无效时不提供search_code，普通聊天继续。配置只留在该请求闭包，模型展示与Runtime执行共享同一最终工具元组，不修改全局TOOL_REGISTRY。其他请求不能用字段相同但不同对象的ToolExecutionContext调用该能力；请求关闭后拒绝执行。非流式/chat没有新增工具能力。

执行前通过本请求ExecutionThreads重新加载当前会话→Task→Workspace关系，错误只公开固定授权失败。选择、查询前授权和召回的同步SQL也显式交给同一跟踪线程集合；HTTP仍在异步事件循环等待，期间不持有Session。工具取消传播并关闭客户端；同步SQL不会被强杀，宿主释放占用前等待线程收尾。独立服务未传execution_threads时保持原有同步短事务行为，未增加后台孤儿任务。

工具成功投影后，on_result仅向当前请求登记批次ID/报告版本；不把内部凭据加入模型参数或Observation。每次聊天模型create之前（包括初始请求和追加Observation后的后续请求），before_send执行：

1. 按实际UTF-8序列化完整messages和tools，包含系统提示、历史、所有当前工具调用/结果与Schema，最多256 KiB；超限返回固定model_request_budget_exceeded，不裁掉来源、不自动重试。它是本地提示字节预算，不是精确Token数、整个HTTP报文字节或独立硬内存隔离；Agent原累计Token预算仍独立生效。
2. 在线程里重新核对当前会话关系，并对本轮所有已登记代码批次按当前归属/绑定、独立配置及报告空间重新预检；删批次、绑定修订改变、会话改变和未知数据库错误均以code_context_send_rejected停止，不能把旧工具结果当成发送许可。
3. 退出全部短事务后复核请求关闭/取消，再调用模型；请求守卫可用于流式/非流式DecisionMaker，但只由本地代码检索绑定开启。失败沿用RUN_ERROR和本轮历史回滚，不保存失败轮次的最终回答。

此复核与外部模型请求不是跨系统原子操作；检查后仍可能发生撤销。绑定修订不跟踪外部文件编辑，批次声明也不证明当前磁盘或供应商别名稳定。守卫追踪本轮代码检索批次，不为跨轮历史回答建立逐片段来源账本，也不替代其他工具自身的发送策略。生成的Embedding查询用量仍在内部结果中，与聊天模型Token/费用不同；本课没有把Embedding费用加入AgentRun统计。

本地请求的额外系统提示要求引用relative_path:start_line、解释窗口/覆盖、不把失败当空结果，并把代码/路径当资料；提示不是注入防御或真实模型遵循保证。不自动扫描/生成/刷新索引，无兼容快照时返回窗口未找到；不增加PC批次、模型空间或技术诊断入口。

验证：新增22项通过——[6项请求绑定](../apps/api/tests/tools/test_code_search_binding.py)验证完整UTF-8精确边界、同值外来Context、关闭/关联变化/未知失败拒绝及取消后等待真实工作线程；[6项模型守卫](../apps/api/tests/model/test_model_request_guard.py)验证流式/非流式请求的通过/拒绝/取消均发生在create前；[10项真实HTTP/PG与受控聊天/Embedding](../apps/api/tests/chat/test_code_search_chat.py)验证模型/Runtime相同能力、配置缺失/无效普通聊天、成功来源往返与落库、空窗口、非法参数/版本错误、结果后绑定变化和首轮/后续提示超限停止发送/不落库、客户端/执行占用释放，以及真实异步工具取消关闭Embedding并等待线程结束。真实供应商、语义质量、浏览器→Next→ASGI跨层断开、Windows未验收；既有归属撤销后占用释放仍失败的边界未改。

16项定向回归通过：线程参数→默认查询授权/HTTP/召回和组合资源关闭/取消，选择test_code_query_search的two_read_transactions（3参数）及external_cancel、test_code_query_context的two_transactions（3参数）、test_code_retrieval_context的three_transactions（3参数）及timeout_and_cancel（2参数）；工具表/提示拼装→Vault聊天match（1项）和本地上下文装配（1项）；模型守卫默认None→非流式与流式工具往返各1项。未扩跑全量/账号专项或前端。13个受影响Python文件Ruff/Pyright通过，核心及配套由教练按本课明确授权完成，不等于学习者独立掌握。

新增复跑（apps/api）：`../../.venv/bin/python -m pytest tests/tools/test_code_search_binding.py tests/model/test_model_request_guard.py tests/chat/test_code_search_chat.py -q --tb=short -W error`。数据库使用根夹具随机测试库/私有schema并自动清理，无开发业务表或真实模型请求。

## 代码检索PC联合验收

[浏览器入口](../apps/web/test/browser/code-search.mjs)与[隔离夹具](../apps/web/test/browser/code_search_model.py)复用生产PC工作台、Next BFF、FastAPI、StreamingDecisionMaker及请求发送守卫；真实PostgreSQL迁移、授权临时Python项目经内部生成→保存准备三份代码快照，空窗口项目不保存批次。模型聊天/Embedding出口和取消通知受控，无真实供应商请求；不在产品添加准备批次或诊断入口。复跑命令和环境见[ENVIRONMENT](../ENVIRONMENT.md#5-定向测试与静态检查)。

覆盖四个场景：命中回答引用实际Observation中的`src/取消.py:1`；最近窗口无兼容批次不发送查询Embedding；供应商503只返回固定失败、不冒充无匹配；等待Embedding正文时点击停止，经同源取消API传播至服务端。每个场景核对1366/1920桌面视口和刷新：只有四次聊天POST，历史事件不变，不重新检索；没有直接访问API端口或技术入口。取消轮不保存消息，其余各保存一问一答。

浏览器结束后独立核对四个Run及六条Message、工具事件数量和占用释放；服务关闭时核对聊天请求次数2/2/2/1、查询次数1/0/1/1、Embedding取消/关闭、聊天流关闭、执行预算释放，以及源文件字节/inode/mtime、绑定修订和完整向量记录不变；退出删除临时项目。忽略目录`apps/web/output/playwright/code-search/`保存截图、evidence.json、database-evidence.json、server-evidence.json、cleanup-evidence.json，各阶段缺一不能判成功。

本轮四场景与独立数据库/关闭核对全部通过；截图已检查，浏览器无pageerror，本轮服务日志未出现ASGI/Next关闭错误。首次尝试因新夹具误读error.details而停止，修正后完整复跑通过；没有据此修改业务错误协议。新Python夹具Ruff/Pyright零错误/警告，ChatPanel导入图类型检查、受影响前端及浏览器脚本ESLint通过。修改点只涉及说明文案和测试接入，不改变运行/BFF/召回业务，回归由本专项覆盖实际受影响的发送前说明、结果渲染和历史刷新，不扩跑全量后端、全量前端或账号专项。

生产修改仅为输入框数据流向说明，说明代码/笔记检索的查询与片段会发送给配置的相应模型；移除不再使用的Vault专用说明常量，历史浏览器脚本同步查找文案。来源由既有Markdown回答呈现，只显示可读路径/行号，不新增点击读取或跳转。上述受控答案验证传输、渲染与持久化，不验证真实模型选择工具/遵循引用的概率、检索语义、提示注入抵抗或当前磁盘版本。停止按钮联合证据不等同于网线断开、关页或文本生成中断网；取消通知由进程内受控通道代替Redis，Windows/生产构建未验收。

## 本地授权查询 API

[注册路由](../apps/api/app/routers/workspace/code_query_context.py)为 `POST /workspaces/{workspace_id}/tasks/{task_id}/code-query-context`。仅APP_MODE=local；复用本机Host、X-Local-Runtime-Token、精确允许Origin及application/json边界，身份来自CurrentUser，Workspace/Task来自32位小写十六进制路径。浏览器使用下述同源代理，内部凭证不能放入浏览器公开配置。

正文只接受以下三个必填字段；示例批次ID和报告版本须替换为实际已授权存储批次的值，接口不会创建或选择批次：

```json
{
    "query": "查找任务取消后的执行占用释放",
    "batch_id": "cccccccccccccccccccccccccccccccc",
    "response_model": "fixture-model-v1"
}
```

- query为严格字符串，非空白、不含NUL或无效UTF-8代理项，最多2000字符/4096 UTF-8字节；保留原始空白与正文。batch_id为32位小写十六进制；response_model沿用Embedding模型名称校验，最多256字符/1024 UTF-8字节，拒绝首尾空白及控制字符。
- 缺字段、类型转换、额外字段、重复JSON键、查询参数及非法路径均在模型请求前拒绝；不接受身份、目录、URL、Key、transport、Top K或预算覆盖。FastAPI会先读取/解析请求正文，再做字段与重复键校验；这里没有独立网络请求体硬上限或解析进程隔离。
- 每次调用延迟加载独立Embedding配置；Top K固定5，上下文预算固定为5片/12000字符/24000字节。没有新增外层Session、扫描、索引写入、结果缓存或重试。既有本机身份依赖会在其短事务中执行users的ON CONFLICT DO NOTHING初始化；查询链路不写批次或向量索引。
- 复用组合服务的发送前授权、事务外HTTP及等待后重新授权。成功响应保留query_sha256、请求/报告模型、本次查询用量/次数、source/query_source和context。公开Schema重新验证来源/片段与预算并拒绝额外内部字段，核对查询摘要、目标批次/Workspace/Task及模型；不返回原查询、向量、连接配置、凭证或宿主根路径。
- context仍完整保留选中片段/坐标/摘要/距离、原批次生成用量与覆盖、召回省略和构建省略。None与已报告0保持区分；距离不是置信度。无候选可明确成功，未知读取/构建/公开投影失败不能伪装为空成功。字符/字节预算只约束context_text，不是整个HTTP响应或最终提示的预算。

成功与匹配路由的失败均为Cache-Control: no-store；错误正文只有固定code/message，不输出供应商体、Key、路径、原始校验错误或未知业务码。

| HTTP | 错误码与含义 |
|---|---|
| 403 / 415 | 既有local_access_rejected/local_mode_required/workspace_origin_rejected或unsupported_workspace_content_type边界拒绝 |
| 422 | invalid_code_query_context_input；内部查询拒绝为embedding_query_invalid/embedding_query_budget_exceeded/code_embedding_query_invalid |
| 404 | workspace_not_accessible，统一覆盖未知/无权/旧绑定/错批次或空间，不区分原因 |
| 503 | embedding_not_configured / embedding_config_invalid，不复用聊天配置 |
| 504 | embedding_timeout |
| 502 | embedding_request_failed / embedding_response_invalid / embedding_response_too_large / code_embedding_query_zero |
| 409 | code_embedding_query_result_invalid（请求后报告版本变化）、code_embedding_batch_inconsistent或code_context_budget_too_small |
| 500 | code_embedding_distance_invalid、code_context_budget_invalid、code_context_query_result_invalid、code_context_snapshot_invalid/code_context_snapshot_too_large；未知异常/业务码/公开投影失败固定为code_query_context_failed |

后置拒绝可能发生在一次成功模型请求之后，不能撤销可能的用量；普通外部取消继续传播，不转换为业务响应。下述BFF已验证显式信号传播，但实际浏览器/Next/ASGI断开是否自动取消上游模型仍待联合验收。HTTP成功仍是旧批次快照，不授予后续文件读取或最终模型发送许可；已有[批次摘要API](code-vector-storage.md#授权批次摘要-api)，真实供应商/语义效果、自动索引、批次保存/删除入口仍未验收；请求级工具与完整messages/tools发送预算见[注册与发送复核](#代码检索请求级注册与发送复核)，PC链路见[联合专项](#代码检索pc联合验收)。

## 同源 BFF 代理

[Next路由](../apps/web/src/app/api/workspaces/[workspaceId]/tasks/[taskId]/code-query-context/route.ts)为 `POST /api/workspaces/{workspaceId}/tasks/{taskId}/code-query-context`，使用Node运行时并等待异步params。[代理](../apps/web/src/app/api/_shared/code-query-context-proxy.ts)复用既有本地模式、回环地址与Origin门禁，只从服务端配置取得API地址和本地凭证；调用方Cookie、Authorization、本地令牌或转发地址不能覆盖它们。要求显式同源Origin、合法路径、无查询参数和application/json；正文仍只接受上述三个字段，实际query不trim或改写。

| 读取对象 | 实际UTF-8字节上限 |
|---|---|
| 请求正文 | 16 KiB |
| 上游成功正文 | 1 MiB |
| 上游错误正文 | 4 KiB |

[有界读取器](../apps/web/src/app/api/_shared/code-query-context-json.ts)逐段计量实际字节，不以Content-Length代替读取证据；拒绝非法UTF-8、JSON BOM、坏语法、解码后重复键、非有限数、无效代理项和超过16层的解析深度。query字符计数与空白判断对齐Python Unicode规则，保留合法正文中的空白。JSON整数声明还必须精确且在JS安全整数范围内；小数舍入成整数、超范围用量不能冒充合法计数，不舍入或回退为零。距离保留有限JS浮点数，不声称任意小数精确表示。

[公开校验器](../apps/web/src/features/workbench/code-query-context-data.ts)逐层拒绝额外字段，并核对查询原文摘要、Workspace/Task/批次、请求及报告模型、模型空间/维度、查询与历史用量、召回计数/顺序/距离、固定服务端预算、覆盖与省略。文件/符号来源摘要须一致；正文摘要与chunk ID重新计算，坐标、分片和规范相对路径须合法。再次解析context_text，核对来源/覆盖声明及选中片段与审计DTO一致，并重新计量实际字符/字节；只有完整通过后才返回独立公开副本，不带原查询、向量、配置、凭证或全部原召回正文。

代理只发送一次请求，禁止重定向，成功失败均no-store，不复制上游Set-Cookie或其他响应头。已知错误必须同时匹配HTTP状态和固定code，message由BFF提供；未知/错配状态、协议、压缩正文、超限或传输失败固定502 code_query_context_failed，不能伪空成功或自动重试。请求输入失败422，客户端显式取消499，代理等待超时504；本地门禁与匹配的后端已知错误保留固定分类。

70秒组合信号覆盖请求正文、fetch和上游正文的异步等待，容纳后端最长60秒模型请求；摘要异步校验后也复核取消，禁止迟到成功发布。正文取消会停止读取、释放reader锁；不用的迟到响应也取消，不无限等待自定义cancel回调。信号不是同步解析的硬CPU时限，流自身缓冲/原生运行时分配也没有独立硬资源隔离；不证明Next将真实浏览器断开转换为Request.signal，或ASGI会取消供应商请求。

校验只证明快照内部一致，不重读磁盘、重建权限或证明语义质量。当前没有PC查询入口、批次保存/删除管理或自动生成/扫描/入库；search_code已接入本地流式Agent并执行下述发送复核；已有批次摘要入口见上述链接，旧结果仍不是未来读取/发送许可。

## 验证入口

在`apps/api`执行：

```bash
../../.venv/bin/python -m pytest -xq --tb=short tests/workspace/files/test_code_context.py
```

[122项纯内存测试](../apps/api/tests/workspace/files/test_code_context.py)通过，覆盖默认投影/完整来源/1与20片、数量上限、实际JSON字符/字节精确边界、中文与转义开销、跳过大片段后选择小片段、双预算原因、重复/冲突/不同文件来源、三层覆盖、部分定义及无命中、空来源声明预算、严格预算与未知对象、输入规模边界/实际超限、模型空间/计数/用量/路径/摘要/坐标/顺序/无效尾部、序列化失败、冻结字段与嵌套副本隔离；构建调用范围封住数据库Session、HTTP客户端和文件读取。

独立构建核心由学习者实现，完成后教练补测试及格式配套，未修改核心行为。相关Pyright零错误/警告、Ruff和Diff空白检查通过；既有生成器/召回/仓储未修改，没有旧行为变化，不另跑旧回归。该课没有连接数据库、真实供应商、浏览器或真实项目读取；后续组合证据见下述范围，模型语义效果、精确Token计量和Windows未验收，工程通过不代表独立掌握。

授权查询上下文组合，在`apps/api`执行：

```bash
../../.venv/bin/python -m pytest -xq --tb=short tests/workspace/files/test_code_query_context_validation.py tests/workspace/files/test_code_query_context.py
```

[55项入口/阶段测试](../apps/api/tests/workspace/files/test_code_query_context_validation.py)验证未知/被绕过构造的严格预算前置拒绝且不反射警告、查询预检、参数及阶段顺序、新预算隔离、查询与历史代码用量分开、未知结果/查询来源/目标/用量错配、失败与取消传播、构建失败不重试或回退；[23项真实PostgreSQL组合](../apps/api/tests/workspace/files/test_code_query_context.py)使用真实HTTPX与MockTransport，验证两个短事务和HTTP退出先于构建、只读SQL、查询等待连接释放、1/2/4096维、精确片段/三层覆盖/部分定义/零候选、发送前当前归属/任务/批次/空间/绑定拒绝、等待期间独立提交撤销/改走再改回/删除、版本变化、后置预算/来源失败、两个事务退出失败及实际超时/取消资源关闭。

本课78项新增通过，Pyright零错误/警告、Ruff和Diff空白检查通过。组合核心与测试由教练按本课明确授权完成；既有生成器、查询召回、Context Builder和仓储未改，不另跑旧回归。数据库仍是根夹具随机库/私有schema、真实提交后自动清理，不修改开发业务表。不是全量后端/账号专项；真实供应商、语义效果、HTTP/PC/Agent装配、完整提示预算和Windows未验收。

本地HTTP入口，在`apps/api`执行：

```bash
../../.venv/bin/python -m pytest -xq --tb=short tests/workspace/files/test_code_query_context_api_validation.py tests/workspace/files/test_code_query_context_api.py
```

[87项无数据库HTTP边界测试](../apps/api/tests/workspace/files/test_code_query_context_api_validation.py)覆盖注册入口/精确参数/公开字段、None/0/已报告用量、严格输入与UTF-8/字符/字节预算、缺失/额外/重复键/坏JSON/查询参数/路径、本地凭证/Host/Origin/媒体/模式、固定状态错误与未知异常、未知/错配/额外私密字段/非有限结果投影拒绝及外部取消传播；[24项真实PostgreSQL API测试](../apps/api/tests/workspace/files/test_code_query_context_api.py)不替换身份依赖，使用真实CurrentUser与HTTPX/MockTransport，验证等待时连接释放、HTTP收尾先于构建、SQL不写批次/向量、分开用量、片段来源/覆盖/零候选/实际预算省略、当前归属/会话/任务/批次/空间/绑定、等待撤销/绑定往返/删除批次、供应商错误/版本变化/坏响应/存储来源失败、实际延迟配置/墙钟超时关闭及坏本地凭证前置拒绝。

本课111项新增与6项定向回归通过；不累计前课数量为全量结果。回归依据为新增子路由注册和WorkspaceRoute输入分类影响既有代码清单边界，选择`test_code_inventory_api.py`中的test_local_access_before_scanner（4参数）、test_account_mode_rejected_before_identity、test_invalid_identifier_uses_safe_existing_boundary。相关Pyright零错误/警告、Ruff与Diff空白检查通过；核心及配套由教练按本课明确授权完成，沿用根夹具随机测试库/私有schema和自动清理，无开发业务表写入。本课未改生成器、组合、构建器或仓储，也未运行前端/浏览器、全量后端或账号专项；未验证边界见API章节。

同源BFF，在`apps/web`执行：

```bash
node --experimental-strip-types --test --test-reporter=spec test/features/workspaces/code-query-context-data.test.ts test/features/workspaces/code-query-context-route.test.ts
pnpm exec tsc --noEmit --strict --skipLibCheck --allowImportingTsExtensions --module esnext --moduleResolution bundler --target ES2017 --lib esnext,dom,dom.iterable \
    src/features/workbench/code-query-context-data.ts \
    src/app/api/_shared/code-query-context-json.ts \
    src/app/api/_shared/code-query-context-proxy.ts \
    'src/app/api/workspaces/[workspaceId]/tasks/[taskId]/code-query-context/route.ts' \
    test/features/workspaces/code-query-context-data.test.ts \
    test/features/workspaces/code-query-context-route.test.ts
pnpm exec eslint \
    src/features/workbench/code-query-context-data.ts \
    src/app/api/_shared/code-query-context-json.ts \
    src/app/api/_shared/code-query-context-proxy.ts \
    'src/app/api/workspaces/[workspaceId]/tasks/[taskId]/code-query-context/route.ts' \
    test/features/workspaces/code-query-context-data.test.ts \
    test/features/workspaces/code-query-context-route.test.ts
```

[116项数据边界测试](../apps/web/test/features/workspaces/code-query-context-data.test.ts)覆盖完整/空/部分定义/省略/Top K/去重快照、查询与历史用量、Unicode字符/字节、严格JSON/整数精度、深层来源/坐标/计数/覆盖/预算错配、正文摘要/ID重算、context_text与审计不一致及副本隔离。[111项路由测试](../apps/web/test/features/workspaces/code-query-context-route.test.ts)使用原生Request/Response/Web Streams与受控fetch，覆盖真实route调用、凭证隔离、路径/Origin/配置门禁、固定状态与code组合、未知/超限/坏UTF-8/压缩响应拒绝、三个正文预算精确边界、不重试、各异步阶段取消/超时、迟到响应、异步摘要后取消及永不结束的cancel回调。

本课227项独立新增用例分批通过，复跑不重复计数。[六份受控快照](../apps/web/test/features/workspaces/code-query-context.fixture.json)来自现有Python纯内存构建器与API公开投影，不代表真实HTTP/数据库链路。BFF及配套由教练按本课明确授权完成，受影响文件TypeScript/ESLint与Diff空白检查通过；仅新增模块，既有代理/后端/页面未修改，不另跑旧回归。该BFF课没有运行全量前端、Next服务、浏览器、数据库或真实供应商；当前产品边界见下节，实际浏览器→Next→ASGI断开、模型语义和Windows仍未验收，工程通过不代表独立掌握。

## 内部查询校验与产品边界

代码检索属于Agent内部机制，产品不提供手动选择向量批次、查询Embedding、预览上下文包或打开技术诊断的入口。当前工作台只保留项目/任务、对话和按需文件改动；来源已通过Agent回答展示易读的文件/行号，不要求用户理解空间ID、预算或审计DTO。产品呈现与当前验收见[PC信息层级](agent-ui-events.md#pc信息层级)。现有后端/API/BFF保留；search_code已在配置有效的本地流式聊天中按模型请求检索已有快照，并在后续聊天模型发送前复核；自动生成索引仍未接入。

[内部浏览器读取实验](../apps/web/src/features/workbench/code-query-context-request.ts)仅验证公开契约，不由产品工作台调用。输入只投影原始query、batch_id、response_model；复用严格请求/深层公开投影，核对查询摘要、目标、批次/报告版本、渲染/来源摘要和所选空间、请求模型、维度、原分块数量/覆盖。正文按浏览器解压后实际1 MiB限量、严格UTF-8/JSON；非200/错误媒体取消未读正文，失败为未知，不反射内部错误、不重试。75秒合并信号覆盖fetch/正文与异步摘要后的复核，释放reader锁，不等待任意cancel回调；取消不撤销模型用量或停止同步数据库线程。

[实验组件](../apps/web/src/features/workbench/components/code-query-context-preview.tsx)保留为内部对照，产品没有导入或挂载。实验中的controller身份、修改查询即失效、资源key/卸载和纯文本投影仍可用于后续有意义的来源展示；不把完成实验或折叠技术字段当成用户功能交付。完整公开包、空间与摘要也不授予最终模型发送许可。

[52项读取层测试](../apps/web/test/features/workspaces/code-query-context-request.test.ts)通过，覆盖六类公开快照、三字段/原查询投影、字符字节与实际正文预算、所选批次交叉事实、深层错配、严格JSON/UTF-8、错误正文不读、前置/正文/摘要后取消、迟到/超时、永不结束cancel回调、锁释放与不重试。在apps/web复跑：

```bash
node --test test/features/workspaces/code-query-context-request.test.ts
pnpm exec eslint src/features/workbench/code-query-context-request.ts \
    src/features/workbench/components/code-query-context-preview.tsx \
    test/features/workspaces/code-query-context-request.test.ts
```

内部读取核心/配套由教练按当课明确授权完成，不代表独立掌握。产品验收使用实际组件与受控BFF响应；真实Next/BFF/API/数据库联合、供应商/语义、浏览器跨层断开与Windows仍未验收。撤下手动入口与后续请求级search_code接入分别验收；本段浏览器证据不证明新检索链路已通过PC验收。
