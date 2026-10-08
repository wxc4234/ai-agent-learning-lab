# 受控代码向量存储与召回协议

当前有[内部授权生成与保存串联](#授权代码生成与保存串联)、单批保存、授权批次读取、指定批次精确召回与显式查询生成→召回串联；显式查询已由[本地上下文API](code-context.md#本地授权查询-api)、[同源BFF](code-context.md#同源-bff-代理)复用；[查询校验实验](code-context.md#内部查询校验与产品边界)不作为用户入口，下述批次摘要已提供只读HTTP入口、[同源BFF](#批次摘要同源-bff)；[产品不暴露手动批次选择](#批次摘要与产品边界)。生成/保存/删除管理仍无HTTP入口，查询尚未接Agent，也没有自动扫描/发送链路。实现见[存储服务](../apps/api/app/services/workspace/files/code_vector_storage.py)、[召回服务](../apps/api/app/services/workspace/files/code_vector_search.py)、[查询串联服务](../apps/api/app/services/workspace/files/code_query_search.py)与[仓储](../apps/api/app/repositories/workspace/code_embedding_repository.py)。存储输入使用[Embedding生成协议](code-embeddings.md)的受控内存结果。

## 调用与授权边界

1. 可信宿主显式选择当前身份、Workspace/Task及允许发送的正文范围。在授权读取与模型请求之前调用 `capture_code_embedding_target`，捕获目录路径和绑定修订；函数只核对数据库归属，不验证磁盘目录是否存在。
2. 捕获事务已结束后，独立完成本轮授权分块和显式Embedding生成。它们不会自动调用存储，也不能把旧结果当作当前授权。
3. `save_code_embedding_batch(target=..., source=..., config=...)` 先在事务外验证全部内存结果，再开启短事务，按项目→任务→会话顺序锁定并重新核对归属、任务范围和绑定。目标路径或修订发生变化时拒绝；改到另一目录再改回来也不能复用旧目标。
4. `load_code_embedding_batch` 必须提供当前身份/项目/任务、批次ID和明确模型空间配置/报告版本。同样持有归属锁，并按当前绑定、指定任务和完整空间指纹过滤；未知、越权、错空间或旧绑定统一拒绝。
5. `search_code_embedding_batch` 消费可信宿主已准备的查询向量，使用与读取相同的当前授权和绑定边界。空间声明不是查询向量真实来源的证明；调用方负责选择批次和保证查询向量来自相同模型空间。

目标DTO、批次ID、分块ID和空间ID都不是许可。当前接口仅供可信宿主使用，不接受模型自行选择目标；任意内存DTO不证明真实文件读取、发送许可或当前文件版本。调用方必须遵守上述顺序；服务校验的是声明的来源一致性，不重新读取磁盘。文件外部修改不会增加数据库绑定修订，摘要只描述生成时的版本。

## 授权代码生成与保存串联

[组合服务](../apps/api/app/services/workspace/files/code_batch_generation.py)提供 `generate_and_save_code_batch(*, user_id, workspace_id, task_id, config=None, transport=None)`。只有可信宿主显式调用时才执行；身份和目标来自当前执行范围，独立Embedding配置由宿主提供或延迟加载，transport只供可信测试注入。没有HTTP、PC按钮、工具注册、自动扫描/刷新或重试。

1. 先检查取消并加载独立配置；未配置时不读源码。短事务捕获当前项目/任务/会话归属、绑定根与修订，成功退出后才开始扫描。
2. 授权Python分块携带 `expected_bound_root` / `expected_binding_revision`。起始根解析、每次目录枚举、忽略规则/正文读取及最终复核都在当前路径授权中比较这两个字段；读取前Session关闭，改走再改回也在文件I/O前拒绝。空分块返回 `code_embedding_chunks_empty`，不构造HTTP客户端、不创建空批次；带预算截断的非空分块仍保留真实覆盖，不补全缺失定义。
3. 首次发送前再捕获并比较目标；[生成器](code-embeddings.md#配置与调用)的 `before_request` 同步检查还在每次HTTP请求前独立结束授权事务。模型等待没有数据库连接/归属锁，中途撤销阻止下一批发送。首次独立前置拒绝保留既有授权/存储错误；生成器内部回调的普通失败统一 `embedding_request_failed`，不反射数据库异常，取消继续传播。
4. 成功退出全部HTTP响应/客户端后检查取消，以及生成结果类型、目标、完整文件/分块来源、策略/解析器和覆盖是否对应本轮。错配返回 `code_embedding_generation_result_invalid`；数值、空间和用量完整校验仍由保存服务负责。保存短事务再次核对当前归属及捕获绑定，空间、批次和全部向量一起commit成功才发布结果。迟到、插入或提交失败不发布成功、不重放，旧批次保留。

返回 `GeneratedCodeEmbeddingBatch`：当前Workspace/Task、`SavedCodeEmbeddingBatch`的批次/空间ID、维度、实际保存数和truncated；生成请求/报告模型、request_count、prompt_tokens/total_tokens；`coverage`保留扫描目录/检查文件/解析文件/符号/生成总分块数、定义行/模块排除行、排除分类计数和原incomplete_reasons。生成总数可能超过选中/保存数；缺失用量为None，报告零保持0。这些计数来自本轮扫描，详细扫描计数仅随组合结果返回，既有批次元数据仍保存来源/截断/生成用量；结果不复制正文、向量、根路径、修订、供应商URL或Key。

此组合不是数据库/文件系统原子快照；绑定修订不跟踪同路径目录替换或外部编辑，生成后文件改变仍可能保存明确的旧内容快照。授权检查与外部请求之间也没有跨系统原子事务；供应商可能已经处理请求，后置拒绝/取消/数据库回滚不能撤销用量。HTTP超时只约束异步模型阶段，扫描、解析、短数据库操作和保存均为同步调用，无独立硬超时；取消不能中断已开始的同步commit，提交确认丢失仍不得盲目重放。尚无最终提示发送或语义效果验收。

## 预检与数值

- 每批1～20个分块、1～20个Python来源文件（各最多64 KiB）、1～4096维；空Embedding结果不能形成存储批次。分块正文1～2000字符、最多4096 UTF-8字节、最多40行；完整校验结果最多2 MiB。
- 拒绝重复来源路径/分块ID、错误文件摘要、正文摘要、策略、半开坐标、分片关系、重叠范围及不一致覆盖标记；重新计算原分块ID。保持原始文件SHA与LF归一化正文SHA的区别。分片可因预算只保留一部分，不强制把截断前缀补成完整定义。
- 维度必须与调用方配置、每条向量及空间身份同时一致。空间指纹复用生成服务的函数，包含协议、规范地址、请求模型、报告版本、维度和dimensions开关；不包含Key。数据库只保存空间ID、模型名与维度，不保存Key或供应商URL。
- 拒绝bool、字符串、NaN/Infinity和float32转换溢出。写入前量化到有限float32；允许正常舍入、下溢和零向量，不声称零向量能参与有效余弦召回。读取时将PG最短十进制文本明确复原为float32数值。
- 保留原文件/符号元数据、实际半开坐标、分片/拆分原因、正文、策略/解析版本、来源信任标签、截断原因、请求次数和用量。缺失用量仍为null，不按零计；生成/入库成功不代表覆盖完整或语义有效。

数值与可变维度依据[pgvector官方类型说明](https://github.com/pgvector/pgvector#vector-type)及[不同维度存储说明](https://github.com/pgvector/pgvector#can-i-store-vectors-with-different-dimensions-in-the-same-column)；SQLAlchemy类型使用锁定的[官方Python适配器](https://github.com/pgvector/pgvector-python#sqlalchemy)。

## 表、事务与生命周期

| 表 | 作用与约束 |
|---|---|
| `code_embedding_spaces` | 空间指纹主键，维度1～4096；并发首次使用由唯一键处理，已存在元数据必须一致 |
| `code_embedding_batches` | 任务归属、私有绑定证据、批次数量与来源元数据；复合外键关联空间/维度 |
| `code_embedding_vectors` | 原顺序、分块ID、向量、正文和来源元数据；批内顺序/ID唯一，复合外键关联批次/空间/维度，CHECK核对实际vector维数 |

使用可变维度 `vector`，目前只有普通关系索引，没有HNSW/IVFFlat索引。4096维已验证保存；这不证明4096维可直接建立相同类型的近似索引。任何未来查询都必须同时限定当前归属、绑定、任务与完整模型空间，不能只过滤维度或模型名。

每次保存都创建独立快照，不覆盖旧批次、不做全项目替换或去重。空间、批次、全部向量在同一事务提交；向量约束、迟到异常或提交失败时全部回滚，已有快照不受影响。模型/文件I/O不在该事务内，模型用量不能靠数据库回滚撤销。

Task删除通过数据库CASCADE清理其批次和向量，沿用既有删除事务；失败时一起回滚，其他任务保留。空间元数据可继续被其他任务使用，删除Task不删除共享空间。没有自动空间垃圾回收、索引刷新、批次选优、幂等请求键或重试；结果未确认时不能盲目重放写入。

## 授权批次摘要 API

[服务](../apps/api/app/services/workspace/files/code_batch_summaries.py)提供 `list_code_embedding_batches(*, user_id, workspace_id, task_id)`；[注册路由](../apps/api/app/routers/workspace/code_batches.py)为 `GET /workspaces/{workspace_id}/tasks/{task_id}/code-embedding-batches`。仅本地模式，身份来自既有本机依赖；Host与内部凭证须通过既有门禁，提供Origin时精确匹配允许列表。路径须为32位小写十六进制，不接受任何查询参数或非空正文，均在业务读取前拒绝；查询/正文拒绝先于身份依赖。不加载Embedding配置，不调用模型、读取项目文件、扫描、保存或删除批次。

短事务按项目→任务→会话锁定并重新授权，即使没有批次也先检查归属。仓储再次联表限制Workspace/Task/Conversation归属、当前根路径和绑定修订，只选择批次/空间列与元数据，不加载ORM批次实体、代码正文或向量。目录改走再改回也不会显示旧绑定批次；未绑定目录明确409，授权的当前绑定确实没有记录时才返回空列表。当前条数/字节预算不提供数据库锁等待或同步操作的硬超时，客户端断开也不保证停止已运行的同步线程。

固定最近20批，按created_at降序、同时间batch_id降序稳定排列，多读1批确定has_more；不查询总数，不提供游标或其他页面。数据库用CASE限制每行source_metadata实际JSON文本最多128 KiB的传输：超限保留批次行和字节数、正文投影为null，随后整次拒绝，不能过滤成不存在。最多21项均须校验，包括不返回的第21项；只保证本轮有界窗口，没有验证更旧的全部批次。数据库计算长度和原生驱动解析没有独立硬CPU/内存隔离。

元数据复用严格来源契约，核对当前Workspace/Task、空间/维度、空间表与来源中的请求/报告模型、文件来源形态、原覆盖、重复原因及历史用量一致性；重新编码也须在128 KiB以内。未知、额外或不一致来源导致固定失败，不发布部分列表。此处不查询向量数量/正文或重新计算空间指纹；chunk_count是保存的批次声明，摘要成功不证明向量可用、当前磁盘版本、语义质量或当前模型配置匹配。

顶层只返回workspace_id、task_id、batches、has_more、limit=20及source=code_embedding_batch_summaries。每项只返回batch_id、space_id、requested_model、response_model、dimensions、chunk_count、truncated、incomplete_reasons、带时区created_at；不带文件路径、来源全文、历史用量、内部主键、私有绑定证据、Key或供应商URL。不同空间可同时出现在列表中，不自动选取最新批次；后续上下文查询仍须显式选择批次/报告版本，并按当前配置与权限重新核对完整空间。

成功退出事务后才返回独立公开数据；HTTP再次严格验证资源、列表顺序/重复项/覆盖与公开字段，匹配路由的成功失败均no-store。没有当前授权或资源时统一404 workspace_not_accessible；本地门禁复用401/403；路径、查询或正文拒绝为422 invalid_code_embedding_batch_list_input；未绑定为409 code_embedding_project_unbound。数据库、坏来源、事务退出、依赖或响应生成的未知失败统一500 code_embedding_batch_list_failed，不反射原始异常或未知业务码，不伪空或重试。既有本机身份依赖仍会幂等初始化users，不能把整个HTTP链路称为只有SELECT。

## 批次摘要同源 BFF

[Next Node路由](../apps/web/src/app/api/workspaces/[workspaceId]/tasks/[taskId]/code-embedding-batches/route.ts)提供 `GET /api/workspaces/{workspaceId}/tasks/{taskId}/code-embedding-batches`，等待异步路径参数后交给[代理](../apps/web/src/app/api/_shared/code-batch-summaries-proxy.ts)。仅本地模式；路径为32位小写hex，不接受查询参数、正文流、非零/不规范Content-Length或任何Transfer-Encoding，均在fetch前拒绝。GET可以省略Origin；有声明时须与请求URL同源，省略时URL来源仍须精确匹配服务端AUTH_ALLOWED_ORIGINS。复用既有本机门禁检查请求来源、Sec-Fetch-Site、本机后端和内部凭证；不接受调用方身份、地址或配置覆盖。

只向服务端配置的本机HTTP API转发一次GET，允许服务端路径前缀，拒绝地址中URI凭证、查询与片段；附加服务端内部token、由请求URL确定的Origin与Accept-Encoding=identity。调用方Cookie/Authorization/token/转发Host不被复制。请求及公开响应no-store、不重试、不跟随重定向；不复制上游Set-Cookie或其他响应头。只接受200或已知错误状态、application/json及未压缩正文，无用响应直接取消而不读取。

成功正文最多64 KiB，错误正文最多4 KiB；复用[有界JSON读取器](../apps/web/src/app/api/_shared/code-query-context-json.ts)，按实际流字节计量，不依赖Content-Length。严格UTF-8与JSON/重复解码键/数值精度等规则见[查询BFF协议](code-context.md#同源-bff-代理)，不通过宽松JSON解析修复坏响应。成功后由[纯公开校验器](../apps/web/src/features/workbench/code-batch-summaries-data.ts)核对完整字段白名单、当前Workspace/Task、source/limit、最多20项及has_more一致性、批次/空间ID、重复批次、模型Unicode字符/字节边界、维度1～4096、数量1～20、覆盖原因唯一且与truncated一致。不同空间可以有不同模型/维度；同一空间的请求/报告模型与维度必须一致。所有返回项都通过后，显式重建独立对象与原因数组，不返回私有字段或部分前缀。

created_at仅接受有效公历、1～9999年、带Z或HH:MM偏移、最多6位小数的当前公开时间格式，保留原字符串。先在安全整数毫秒上转换BigInt，再补微秒并折算时区；按真实瞬间降序，同瞬间按batch_id降序核对。不能用会丢失微秒、规范化无效日期的Date.parse，也不能按带不同时区的原字符串排序。

已知上游错误必须同时匹配状态与code：401 invalid_login_session；403 local_mode_required/local_access_rejected/workspace_origin_rejected；404 workspace_not_accessible；409 code_embedding_project_unbound；422 invalid_code_embedding_batch_list_input；500 code_embedding_batch_list_failed。仅保留固定code与本地消息，忽略原始message与私有附加字段；未知状态/码、错配、坏目标、坏JSON/UTF-8、超限和传输失败统一502 code_embedding_batch_list_failed，不伪空成功。代理输入拒绝为422，调用方取消为499，等待预算耗尽为504；取消/超时同样使用固定失败码。

20秒预算从代理准备上游请求时开始，与request.signal合并，覆盖fetch及成功/错误正文等待；fetch前、fetch返回后、读取/公开投影后复核信号。读取失败/取消释放reader锁，迟到响应取消正文，不等待任意cancel回调。字节和异步等待预算不提供同步解析、校验或原生流缓冲的独立硬CPU/内存隔离，也不保证后端同步数据库线程停止；真实浏览器→Next→ASGI断开仍待联合验收。

此入口只转发已有摘要，不扫描、生成、保存、查询向量或自动选择批次。产品边界见下节，尚无Agent及最终提示链路。摘要依然只是当前授权/绑定下的选择快照，不证明当前模型匹配、磁盘版本、向量可用或语义效果；后续查询和最终模型发送不能跳过当前授权与完整预算复核。

## 批次摘要与产品边界

批次/模型空间是内部检索机制，产品不提供手动批次列表或选择入口，也不会为了填充列表自动生成/保存批次。API/BFF摘要只作为有界候选快照，仍不能证明当前模型匹配、磁盘版本、向量可用或最终发送许可；查询需由后续Agent内部链路显式确定批次并重新授权完整空间。产品当前呈现见[PC信息层级](agent-ui-events.md#pc信息层级)。

[内部读取实验](../apps/web/src/features/workbench/code-batch-summaries-request.ts)只GET同源BFF，按浏览器解压后的实际正文限64 KiB，复用严格JSON/公开摘要校验；25秒预算/取消覆盖fetch、正文与投影后复核，失败不读内部错误正文，不自动重试。[对照组件](../apps/web/src/features/workbench/components/code-batch-summaries-panel.tsx)保留为内部实验，工作台没有导入或挂载；controller身份、当前成功窗口/资源key、重读或卸载即失效的机制不构成用户操作需求。

## 指定批次精确召回

- 调用必须显式提供当前身份、Workspace/Task、批次ID、配置、报告模型版本和查询向量；`top_k` 默认5，严格整数1～20。查询只接受与配置维数相同的tuple，沿用存储的有限float32规则；预检在数据库事务前完成，零向量（包括量化后全零）整次拒绝。
- 短事务按项目→任务→会话锁定并重新授权，批次必须匹配当前路径/绑定修订和完整空间指纹。授权/空间失败发生在距离查询之前；SQL再次限定批次内部ID、空间与维度，不从其他批次借候选。没有模型调用、文件读取或业务数据写入。
- pgvector `<=>` 计算余弦距离，升序后用原始ordinal、chunk ID稳定排序。当前每批最多20条，SQL最多读取21行用于发现异常预算/数量；全部有界候选校验后再取Top K，不能用提前LIMIT K隐藏尾部无效距离。这是小批次精确扫描，没有HNSW/IVFFlat，也不承诺整个项目的语义召回率。
- 批内零向量通过CASE保留为无距离候选，排除并计数；全零批次返回明确的空结果。非零候选距离必须为非bool数值、有限且在0～2内；有限向量也可能产生无效距离，出现未知/NaN等整次拒绝，不返回部分命中。可空距离先显式排除None，再转换为float。
- 返回批次/空间、维度、Top K、候选数量、零向量排除数量和`omitted_by_top_k`；每条命中带从1开始的rank、距离、原始正文及完整chunk来源。距离不是置信度，不代表代码正确、当前文件版本或语义质量。
- 完整保留批次metadata中的文件摘要、策略、来源信任标签、用量未知值、truncated与原因。Top K省略量表示本次选择；原始truncated表示生成覆盖，两者不互相替代。返回深复制的普通快照，退出事务成功后才返回，不暴露ORM、Key、供应商URL或绑定绝对路径。

可信宿主也可通过下述串联入口生成查询向量并执行召回，显式将选定快照交给[有界Context Builder](code-context.md)，或调用[授权查询上下文组合](code-context.md#授权查询上下文串联)。本地查询HTTP已复用该组合，契约与验收见[API协议](code-context.md#本地授权查询-api)和[同源BFF](code-context.md#同源-bff-代理)；索引重建、混合排序与PC展示未装配。算子与排序依据[pgvector查询说明](https://github.com/pgvector/pgvector#querying)及[Python SQLAlchemy适配器](https://github.com/pgvector/pgvector-python#sqlalchemy)；数据库距离校验不能替代真实模型/固定任务集的语义评测。

## 授权查询与召回串联

`search_code_query(query, *, user_id, workspace_id, task_id, batch_id, config, response_model, top_k=5, transport=None)` 只消费可信宿主明确选定的查询文本、当前身份、目标批次与预期模型空间。`response_model`是宿主预期的供应商报告版本，与独立配置共同确定完整空间；不按返回版本自动切换批次。transport仅供可信测试注入，不对外开放。

1. 在事务外复用[单查询预检](code-embeddings.md#独立查询-embedding)，并检查严格整数Top K 1～20及预期模型版本。无效输入不开Session、不发送请求。
2. 第一段短事务按项目→任务→会话锁定当前归属，读取匹配指定任务、批次、完整空间及当前目录路径/绑定修订的批次，核对维度、数量和元数据声明。此阶段不读取向量/正文、不计算距离；未知、越权、错空间、旧绑定或元数据不一致均在发送前拒绝。只有成功退出Session后才继续。
3. 在所有Session关闭后调用独立查询生成，完整关闭HTTP响应与客户端后取得结果；等待时没有数据库连接或归属锁。复核结果类型、原文摘要、请求/报告模型、严格维度、空间、一次请求来源及已报告/未知用量。不接受未知对象或错配声明；报告版本发生变化返回`code_embedding_query_result_invalid`，不查询距离或回退旧向量。
4. 现有召回入口先完成float32/零查询向量预检，再开启第二段短事务，重新授权当前项目/任务/会话、绑定与同一批次/空间，并验证全部有界候选。模型等待期间归属撤销、重绑定、改走再改回、任务/批次删除或批次异常均不能凭第一段检查放行。成功退出第二段事务后才发布结果，失败不返回部分命中。

返回`CodeQuerySearchResult`：查询摘要、请求/报告模型、用量、request_count=1、source=query_embedding及原召回快照。查询正文、查询向量、供应商地址/Key、宿主绑定路径和ORM对象不进入投影；命中的授权代码正文与原来源/覆盖字段仍保留。缺失用量保持None，已报告零保留0；距离不是置信度，批次快照不是当前磁盘版本，旧结果不是后续模型发送许可。

没有自动扫描、索引更新、重试或Agent装配；内部串联由[本地上下文API](code-context.md#本地授权查询-api)复用。生成失败/超时/取消沿用原生成错误和资源关闭契约；模型超时只约束HTTP阶段，两个短数据库操作仍为同步调用。第一段事务退出失败不能发送，第二段退出失败不能发布成功；后置拒绝或取消不能撤销供应商已处理的请求和可能产生的用量。

## 迁移与环境

[迁移](../apps/api/migrations/versions/6f4c2b8d901a_add_code_embedding_storage.py)显式安装/复用public中的vector扩展并创建三表。需要扩展已在服务器安装以及创建扩展的数据库权限；已在其他schema安装时拒绝，不偷偷移动它。当前Compose镜像提供该扩展，依赖安装和向前升级按[环境说明](../ENVIRONMENT.md#3-数据库服务与迁移)执行；API启动不自动DDL。

降级先锁三表，任一表非空即拒绝丢失快照/空间元数据；空表可降级再升级。降级保留共享vector扩展，不操作其他schema。数据库测试仍仅用根夹具的随机数据库/私有schema，public只提供共享vector类型和函数，应用表仍在私有schema。

既有浏览器隔离API初始化已在真实迁移后用独立进程核对版本、表隔离及vector类型/函数/操作符；该证据只证明初始化，不等于完整PC流程验收。精确召回复用现有三表，不新增迁移或修改开发业务表。

## 验证入口

授权代码生成与保存组合，在 `apps/api` 执行：

```bash
../../.venv/bin/python -m pytest -xq --tb=short tests/workspace/files/test_code_batch_generation.py
```

[56项组合专项](../apps/api/tests/workspace/files/test_code_batch_generation.py)通过，使用临时Python源码、真实HTTPX/MockTransport和根夹具的随机PostgreSQL库/私有schema，真实提交并自动清理。覆盖原文件SHA/CRLF归一化、过滤/模块行排除、公开事实与未知/零用量、1/4096维、9/20/21分块及多请求/覆盖截断、追加快照、初始归属/未绑定拒绝、空分块/语法/配置失败；捕获后、读取前、分块后及模型等待期间撤销/重绑定/往返/删除；首批后撤销阻止后续请求；坏响应、float32溢出、后批版本变化与旧批次保留；插入/提交/前置事务退出/HTTP客户端退出失败、实际超时、取消及吞取消的迟到响应、生成结果来源错配。模型等待和文件I/O阶段检查连接已释放，客户端关闭先于插入。

23项直接受影响回归通过。修订参数传递影响路径/读取/枚举/扫描/分块默认调用，选择 `test_workspace_path.py::{test_owned_task_reads_only_and_closes_before_filesystem,test_direct_path_rejects_internal_alias_but_default_still_allows}`、`test_workspace_file.py::{test_real_authorization_to_read,test_real_authorization_rejection_precedes_read}`、`test_workspace_listing.py::test_authorized_listing_closes_session_before_scan`、`test_code_inventory.py::{test_typed_sorted_metadata_exact_bytes_and_no_writes,test_hard_exclusions_cannot_be_restored_and_never_opened}`、`test_python_chunks.py::{test_nested_definitions_own_disjoint_lines_decorators_and_exact_sources,test_only_filtered_python_content_is_parsed_and_source_is_read_once}`及`test_python_chunks_authorization.py::{test_authorized_local_source_is_read_only_and_all_sessions_close_before_io_and_chunking,test_change_after_collected_chunk_discards_entire_result}`；发送前回调影响默认模型循环，选择 `test_code_embeddings.py::{test_actual_request_and_reordered_indexes_keep_all_provenance_without_sending_metadata,test_item_budget_batches_and_preserves_input_order,test_later_batch_failure_stops_without_returning_or_retrying_earlier_vectors,test_total_wall_clock_timeout_closes_active_response,test_external_cancellation_propagates_and_closes_response}`。花括号为用例选择说明，运行时分别用pytest的`文件::用例`路径；不扩大到整个领域。

受影响Pyright零错误/警告、Ruff与Diff空白检查通过；本课核心及配套由教练按当课明确授权实现，工程通过不等于学习者独立掌握。没有迁移/开发业务表写入、真实项目发送、供应商/语义、HTTP写入口、Agent、浏览器或Windows验收；界面未改动，不重复前端专项。

在 `apps/api` 执行：

```bash
../../.venv/bin/python -m pytest -q tests/workspace/files/test_code_vector_storage_validation.py tests/workspace/files/test_code_vector_storage.py tests/migrations/test_code_embedding_migration.py
```

[71项预检测试](../apps/api/tests/workspace/files/test_code_vector_storage_validation.py)验证无效批次不开事务、静态拒绝、跨平台路径、定义分片完整性及元数据/数值矩阵；[42项真实PostgreSQL测试](../apps/api/tests/workspace/files/test_code_vector_storage.py)验证类型和float32、1/2/4096维、精确正文/数量预算、模型空间、当前归属与绑定、并发跨项目、真实提交及晚期/提交/子插入失败回滚、任务级联与回滚；[3项迁移测试](../apps/api/tests/migrations/test_code_embedding_migration.py)验证真实升级/元数据一致、空表降级再升、非空拒绝和DDL失败回滚。

定向回归只包含生成投影/空间/空输入3项、任务删除成功/回滚5项、启动版本只读检查4项。共116项新增与12项回归通过；不是全量后端或账号模式专项。真实供应商、语义效果、自动项目索引、召回/上下文、浏览器和Windows未在本课验收。

指定批次召回，在`apps/api`执行：

```bash
../../.venv/bin/python -m pytest -xq --tb=short tests/workspace/files/test_code_vector_search_validation.py tests/workspace/files/test_code_vector_search.py
```

[41项召回预检](../apps/api/tests/workspace/files/test_code_vector_search_validation.py)验证无效参数/零查询向量不开事务、静态拒绝与距离边界；[41项真实PostgreSQL召回](../apps/api/tests/workspace/files/test_code_vector_search.py)验证实际余弦排序、稳定并列、1/2/4096维、20条边界、原始分片/覆盖和深复制、只读SQL、零候选、精确批次/空间/当前归属/绑定、元数据与候选不一致、Top K不能隐藏无效尾部距离及事务退出失败。

召回课82项新增与3项定向回归通过，受影响Pyright/Ruff通过。既有回归只选仓储共用导入影响到的受控生成→保存→读取，以及不同供应商/维度的空间拒绝：`test_code_vector_storage.py::test_controlled_generation_then_atomic_storage_roundtrips_provenance`、`test_same_task_separates_model_spaces_and_rejects_wrong_read[provider]`、`test_same_task_separates_model_spaces_and_rejects_wrong_read[dimensions]`。不是全量后端/账号专项；查询生成另见[独立协议与证据](code-embeddings.md#独立查询-embedding)，授权串联另见下述证据，内存选择及查询上下文组合另见[上下文协议](code-context.md)。查询HTTP与BFF证据另见[API协议](code-context.md#本地授权查询-api)和[代理协议](code-context.md#同源-bff-代理)；真实供应商、自然语言语义效果、自动索引、PC和Windows仍未验收。

授权查询串联，在`apps/api`执行：

```bash
../../.venv/bin/python -m pytest -xq --tb=short tests/workspace/files/test_code_query_search_validation.py tests/workspace/files/test_code_query_search.py
```

[43项入口/结果预检](../apps/api/tests/workspace/files/test_code_query_search_validation.py)覆盖查询及预算、Top K、版本、未知生成对象、摘要/空间/来源/次数/用量错配；[57项真实PostgreSQL串联](../apps/api/tests/workspace/files/test_code_query_search.py)组合真实HTTPX与MockTransport，覆盖两个短事务及只读SQL、模型等待连接释放/独立提交、HTTP退出先于距离查询、1/2/4096维与20候选、精确批次/Key轮换/来源覆盖/零候选、发送前当前归属/绑定/空间/元数据拒绝、等待期间撤销/重绑定/删除/异常、版本变化、float32/零查询及实际无效尾部距离、生成失败/实际超时/取消/客户端退出、两个事务退出失败。

本课100项新增用例分别通过，相关Pyright零错误/警告、Ruff及Diff空白检查通过。串联核心与测试由教练按本课明确授权实现；没有修改既有生成器、召回、仓储或协议类型，不另跑旧回归。数据库使用随机测试库/私有schema并自动清理；无开发业务表修改、真实供应商、真实项目发送或浏览器验收。工程通过不代表独立掌握或自然语言召回质量。

批次摘要查询，在`apps/api`执行：

```bash
../../.venv/bin/python -m pytest -xq --tb=short tests/workspace/files/test_code_batch_summaries_validation.py tests/workspace/files/test_code_batch_summaries.py
```

[61项纯校验/HTTP边界](../apps/api/tests/workspace/files/test_code_batch_summaries_validation.py)验证非法服务参数前置拒绝、严格公开Schema、固定参数/目标、路径、禁止查询/正文、本机Host/凭证/Origin、模式、依赖/授权/未知业务错误、绕过构造与响应生成失败。[34项真实PostgreSQL及本地身份](../apps/api/tests/workspace/files/test_code_batch_summaries.py)验证授权空列表、公开字段/原覆盖、SQL不读向量/正文且不写批次、20/21/23项实际限量/稳定同时间排序、任务/模型空间隔离、当前归属/会话/任务、绑定修订/往返/解绑、新旧批次、坏/超限来源及第21项拒绝、数据库元数据字节精确边界、事务退出失败和pg_blocking_pids确认的锁等待后撤销/改绑定。

API课95项独立新增用例分批通过，另有5项直接受影响回归：新增公共路由分类影响既有GET输入拒绝、GET失败脱敏和POST专用错误，分别选`test_code_inventory_api.py::test_invalid_identifier_uses_safe_existing_boundary`、`test_task_sample_status_api.py::test_failures_are_not_missing_or_ready`（3参数）及`test_code_query_context_api_validation.py::test_query_parameters_and_invalid_path_are_sanitized`；未修改既有仓储函数，不回归无关存储/召回。相关Pyright零错误/警告、Ruff与Diff空白检查通过；核心及配套按当课明确授权由教练完成。复用根夹具随机测试库/私有schema、真实提交并自动清理，没有开发业务表写入。该课没有全量后端、账号专项、前端/BFF/浏览器或真实供应商验收；BFF/产品边界见上，批次保存/删除入口和Windows仍待实现或验证，不代表独立掌握。

批次摘要BFF，在`apps/web`执行：

```bash
node --experimental-strip-types --test --test-reporter=spec test/features/workspaces/code-batch-summaries-data.test.ts test/features/workspaces/code-batch-summaries-route.test.ts
pnpm exec tsc --noEmit --strict --skipLibCheck --allowImportingTsExtensions --module esnext --moduleResolution bundler --target ES2017 --lib esnext,dom,dom.iterable \
    src/features/workbench/code-batch-summaries-data.ts \
    src/app/api/_shared/code-batch-summaries-proxy.ts \
    'src/app/api/workspaces/[workspaceId]/tasks/[taskId]/code-embedding-batches/route.ts' \
    test/features/workspaces/code-batch-summaries-data.test.ts \
    test/features/workspaces/code-batch-summaries-route.test.ts
pnpm exec eslint \
    src/features/workbench/code-batch-summaries-data.ts \
    src/app/api/_shared/code-batch-summaries-proxy.ts \
    'src/app/api/workspaces/[workspaceId]/tasks/[taskId]/code-embedding-batches/route.ts' \
    test/features/workspaces/code-batch-summaries-data.test.ts \
    test/features/workspaces/code-batch-summaries-route.test.ts
```

[87项数据测试](../apps/web/test/features/workspaces/code-batch-summaries-data.test.ts)验证空/完整/覆盖不全/20项/has_more、模型/时间边界、字段白名单/目标/数量/覆盖、微秒/时区/并列顺序、尾部坏项、重复ID、同空间一致/不同空间差异与副本隔离。[77项路由测试](../apps/web/test/features/workspaces/code-batch-summaries-route.test.ts)使用原生Request/Response/Web Streams及受控fetch，验证注册GET、服务端路径前缀/凭证隔离、可省略Origin和输入/模式/配置门禁、固定状态与码/错误脱敏、实际成功/错误字节预算精确边界、严格JSON/UTF-8/分片、媒体/压缩/状态拒绝、fetch与两种正文等待期间的取消/模拟超时、迟到响应、永不结束cancel回调、流失败和不重试。

BFF课164项独立新增用例分批通过，不重复计数。七份[受控快照](../apps/web/test/features/workspaces/code-batch-summaries.fixture.json)由现有Python公开DTO序列化产生，不代表真实HTTP/数据库链路。核心与配套由教练按本课明确授权完成，受影响TypeScript/ESLint及空白检查通过；只新增模块，既有后端、共享读取器/门禁、代理和页面未修改，不另跑旧回归。该课未运行全量前端、Next服务、浏览器、数据库或真实供应商；产品边界见上，实际跨层断开、语义效果和Windows仍待验收，工程通过不代表独立掌握。

内部浏览器读取实验，在`apps/web`执行：

```bash
node --test test/features/workspaces/code-batch-summaries-request.test.ts
```

[37项读取层测试](../apps/web/test/features/workspaces/code-batch-summaries-request.test.ts)已通过，覆盖七种公开DTO、实际字节/严格JSON/UTF-8、目标与额外字段、失败未知、错误正文不读、前置/正文/投影后取消、超时/迟到、锁释放与不重试；这是公开读取契约实验，不是当前产品的手动批次选择功能。实际工作台已撤下此入口，当前产品验收见[UI协议](agent-ui-events.md#当前产品界面验收)。内部契约由教练按相应课明确授权实现，不代表独立掌握；真实跨层断开、供应商/语义和Windows仍未验收。
