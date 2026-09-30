# 授权代码清单、Python 符号、检索与分块协议

## 入口与来源

`GET /workspaces/{workspace_id}/tasks/{task_id}/code-inventory` 仅用于 `APP_MODE=local`，沿用本机身份、内部访问凭证、Host/Origin 和任务归属边界。两个标识必须是32位小写十六进制；不接受任何查询参数，客户端不能覆盖身份、目录、预算或忽略规则。成功响应设置 `Cache-Control: no-store`。

当前入口只供后端调用，没有浏览器 BFF、工具或 Agent 接入。服务不要求绑定目录是 Git 仓库，不执行 Git、仓库代码或外部命令，不创建索引、Embedding 或数据库业务记录。现有本机身份依赖可能执行用户表的幂等初始化，因此“只读扫描”不表示请求完全没有 SQL 写语句。

每次枚举、读取和最终复核都重新检查 Workspace、Task、Conversation 的归属及绑定根，要求当前根路径与本轮起始路径一致。授权查询的 Session 在文件系统 I/O 前关闭，不跨扫描持有数据库事务。读取沿用逐段无跟随描述符，拒绝符号链接与特殊文件，不因未绑定而回退到进程工作目录。

## 响应

| 字段 | 含义 |
|---|---|
| `source` / `policy` | 固定为 `authorized_code_inventory` / `gitignore_subset_v1` |
| `workspace_id` / `task_id` | 本次授权范围 |
| `files` | 按相对路径排序的候选；每项含 `relative_path`、`file_type`、`language`、`byte_count`、`sha256` |
| `file_type` | `source` 或 `configuration`，按名称/扩展名分类，不证明语法有效 |
| `byte_count` / `sha256` | 同一次受限 UTF-8 读取的原始字节数和 SHA-256，保留 CRLF；不返回正文 |
| `scanned_directories` | 实际枚举的目录数量，含绑定根 |
| `inspected_files` | 实际尝试读取的源码/配置候选数量；二进制、超限和疑似密钥候选也计数，不含规则文件 |
| `excluded_counts` | 本次遇到的排除项计数；被剪枝目录仅计该入口，不估算其所有后代 |
| `truncated` / `incomplete_reasons` | 覆盖不完整标记及原因，不能将空清单解释为整个项目没有代码 |

排除计数使用 `hidden`、`link_or_special`、`dependency_or_generated`、`sensitive_name`、`gitignore`、`unsupported_type`、`non_text_or_oversized`、`suspicious_content`；只返回实际出现的计数。路径是绑定根内的相对 POSIX 路径，响应不包含宿主绝对路径、规则正文或源码正文。

## 忽略规则子集

只读取实际进入目录的 `.gitignore`，父规则在前、子规则在后，同级最后匹配生效。被忽略的目录不再进入，内部否定规则不能恢复其后代。这些语义依据 [Git 官方忽略规则](https://git-scm.com/docs/gitignore)，实现不是完整 Git 兼容层。

- 支持 LF/CRLF、空行、行首 `#` 注释，保留前导空格，移除未转义尾部空格。
- 支持行首 `!` 否定、`/` 根锚定、尾部 `/` 仅目录。无路径分隔符的模式匹配任意层级的名称；含分隔符的模式相对于规则所在目录。
- 支持每段中的 `*`、`?`、普通 ASCII 字符集/升序范围及 `[!ab]` 否定字符集；普通通配不跨 `/`。按 UTF-8 字节匹配，因此 `?` 不表示一个 Unicode 字符。
- 支持独立路径段 `**`，例如 `**/a.py`、`a/**/b.py`、`a/**`；最后一例匹配目录内容而非 `a` 本身。
- 只支持行首 `\#`、`\!` 转义；其他反斜线、POSIX 字符类、复杂字符集、部分路径段中的 `**`（例如 `a**b`）、BOM、模式内 ASCII 0～31 控制字符、空路径段、`.`/`..` 等形式明确拒绝。

规则最多16 KiB、200行，单个有效模式最多1024字节/16段。任何已观察规则不能读取、不是普通文件、编码非法、超限或语法不支持时，整次失败，不把它当成空规则。即使限量目录枚举没有列出 `.gitignore`，也会独立尝试读取；只有授权解析时明确不存在才记录为缺失。

不读取 `.git/info/exclude`、全局忽略配置或 Git 索引；不采用 `core.ignorecase`。因此 Git 已跟踪但符合本扫描策略的文件仍会被排除。未知语法会使整个请求失败，取舍是支持范围较小，避免静默纳入本应忽略的候选。

## 固定排除与预算

隐藏项（包括 `.git`、`.env`）不参与候选；`.gitignore` 仅作为策略文件读取。符号链接、FIFO 等特殊项也不参与。名称比较对以下固定排除项忽略大小写，否定规则不能恢复它们：

- 依赖/产物目录：`node_modules`、`vendor`、`venv`、`build`、`dist`、`out`、`coverage`、`target`、`__pycache__`。
- 生成文件：`package-lock.json`、`pnpm-lock.yaml`、`yarn.lock`、`poetry.lock`、`uv.lock`、`cargo.lock`。
- 疑似敏感名称：`secret`、`secrets`、`credential`、`credentials`、`password`、`passwords`、`token`、`tokens`、`api_keys`、`service_account`、`service-account` 文件名主体，以及 `secrets.`、`credentials.`、`id_rsa`、`id_ed25519` 前缀。

候选语言和配置扩展名的唯一列表位于 [扫描服务](../apps/api/app/services/workspace/files/code_inventory.py)。严格 UTF-8、无 NUL、单文件不超过256 KiB；不符合时明确计入排除。内容出现私钥标记、部分常见密钥格式或字面量凭证赋值时保守排除；这是启发式，会有误判和遗漏，不能保证识别所有敏感内容或生成物，也不改变通用文件工具的访问策略。

| 预算 | 上限与覆盖原因 |
|---|---|
| 目录 | 20个，含根；遗漏目录记 `directory_budget` |
| 深度 | 根为0，最大8；遗漏更深目录记 `depth_budget` |
| 单目录条目 | 200条，沿用有界枚举；记 `directory_entries` |
| 候选读取 | 20次；遇到额外可读候选才记 `file_budget`，仅达到20次本身不证明遗漏 |
| 相对路径 | 最多4096 UTF-8字节，并沿用路径输入校验；不支持项记 `unsupported_path` |

每个进入目录的规则文件在开始和最终复核时各尝试读取一次。规则读取沿用共享文件读取的256 KiB上限（含一个超限探测字节）；16 KiB是解析接受上限。规则 I/O 不占20次候选预算，但由20个目录、两次读取和单次字节上限共同限制。扫描量有界不等于普通文件 I/O 有超时保证。

## 失败与一致性

归属不足与不存在沿用统一404；未绑定、非法路径或未知规则422；观察到绑定、文件或规则变化409；访问拒绝403；平台缺少安全文件能力501；已知不可用503；未知异常500且返回固定错误，不反射系统异常。失败不返回先前取得的部分候选；只有定义过的预算不足和排除分类可以随成功响应返回。

返回前重新读取所有已观察 `.gitignore`（包括缺失状态），摘要/缺失状态不同则返回 `code_ignore_changed`；最后再次核对归属与根路径。这不是跨数据库、目录树和文件内容的原子快照，无法识别所有短暂变化、同路径目录替换或绑定先变更再恢复。来源摘要只代表本次各文件读取，旧清单不是后续读取/索引/发送给模型的许可。

## Python 符号清单

`GET /workspaces/{workspace_id}/tasks/{task_id}/python-symbols` 沿用上面的本地身份、归属、忽略、排除、读取与最终复核边界，也不接受查询参数。它重新发起一次授权扫描，内部只读消费者直接解析本次已经过滤的内存正文，不凭旧清单触发再次打开，不返回正文或持久化索引。

只解析 `language=python` 的源码候选（含 `.py`、`.pyi`）；依赖、链接、忽略项和疑似敏感内容不会进入解析器。非 Python 候选仍占共享候选读取预算，因此20次读取不表示能解析20个 Python 文件。仅有非 Python 候选且覆盖完整时，可以返回无 Python 文件/符号；有预算遗漏时必须保留覆盖原因。

响应共用范围、扫描计数、排除计数与覆盖字段，以下字段有专门含义：

| 字段 | 含义 |
|---|---|
| `source` / `policy` | `authorized_python_symbols` / `gitignore_subset_v1` |
| `parser` | API解释器的AST语法版本；当前验证为 `python_ast_3_12`，不推断源码的目标运行版本 |
| `files` / `parsed_files` | 实际成功解析的 Python 文件元数据及数量；没有定义的文件也保留 |
| `symbols` | 按相对路径/定义行排序的函数、异步函数、类；不含变量、import、lambda或引用关系 |
| `name` / `qualified_name` | AST名称与词法嵌套名称，如 `Outer.run.inner`；不是运行时属性路径，同名定义不合并 |
| `kind` | `function`、`async_function`、`class`；类中的方法仍按函数分类 |
| `relative_path` / `sha256` | 所属文件相对路径和同次读取的整文件摘要，与 `files` 一致 |
| `start_line` / `definition_line` / `end_line` | 1起始、两端包含；起始含最早装饰器，定义行为 `def`/`async def`/`class` 所在行，结束为AST定义体末行，不包括后续空行或独立注释 |

解析通过不证明代码可以执行，也不检查运行时引用、导入成功或分支是否发生。服务只使用 `ast.parse` 取得语法树和定义位置，不调用源码的 import、装饰器、函数、`exec`/`eval`；行号支持LF/CRLF/CR，摘要仍对应原始字节。AST能力与资源风险依据 [Python 3.12 官方文档](https://docs.python.org/3.12/library/ast.html#ast.parse)。

### 解析与输出预算

| 阶段 | 固定上限 |
|---|---|
| 解析输入 | 每篇64 KiB UTF-8；实际文件读取仍沿用256 KiB共享上限，64 KiB是解析接受上限 |
| 词法预检 | 每篇4096个token、每逻辑行256个token、括号嵌套32、缩进嵌套32 |
| AST遍历 | 每篇8192次节点访问、最大深度64；模块深度为0，使用显式迭代器栈 |
| 名称 | 词法限定名称最多1024 UTF-8字节 |
| 符号结果 | 全请求最多200个；观察到额外定义才记 `symbol_budget`，与扫描覆盖原因合并 |
| 完整输出 | JSON序列化后最多64 KiB UTF-8，含全部路径、摘要、计数与覆盖标记 |

词法token数包含产生的注释/换行/缩进/结束等token；括号外的NL或NEWLINE重置逻辑行计数，括号内跨行合计。预检不是完整语法检查，错误token/词法异常或AST语法异常均拒绝。解析发生在API进程内，这些预算减少资源风险，没有独立进程隔离或硬超时，不能宣称构成解析Sandbox。

语法失败422 `python_syntax_invalid`；输入/词法/AST/名称预算及捕获的递归或内存失败422 `python_parse_budget_exceeded`；无法确认行号422 `python_symbol_invalid`；完整输出超限422 `python_symbols_result_too_large`。不返回原始解析异常、源码行或已收集的部分符号。符号数量上限是覆盖截断，不是语法失败；即使输出已满，仍校验后续AST并完成扫描的最终策略/归属复核。未知失败返回500 `python_symbols_failed`，其他共享错误沿用前面的映射。

多篇顺序读取与解析不是原子项目快照；文件在受限读取结束后被编辑时，符号仍描述当次内容。旧符号、摘要或 `parser` 字段都不是后续文件访问许可。输出子集取决于限量枚举与扫描顺序，不保证覆盖所有文件或按全项目路径取前200个定义。

## Python 符号检索与定义引用

`GET /workspaces/{workspace_id}/tasks/{task_id}/python-symbol-search?query=Outer.run` 沿用本地身份、任务归属与当前扫描策略，只接受恰好一个 `query`；缺失、重复或任何额外参数均422。原始值与NFKC规范化值各不超过1024 UTF-8字节，必须是由ASCII点号连接的非空标识符片段；不修剪空格、不接受正则、路径或模糊匹配。

无点号按 `name`、有点号按 `qualified_name` 精确且区分大小写匹配，NFKC与 [Python标识符解析](https://docs.python.org/3.12/reference/lexical_analysis.html#identifiers)一致。查询是名称数据，不是待执行Python语句；保留字也可查询，以支持原始Unicode标识符规范化后与保留字同名的AST名称。返回原始 `query` 与 `normalized_query`，不隐瞒规范化。同名定义、不同文件或词法作用域的结果保留各自来源，不搜索调用/引用关系、变量或任意文本。

检索重新发起授权扫描，从经过忽略/敏感过滤的同次内存正文解析，内部符号消费者在200项清单输出截断前检查每个定义。不能先截断符号清单再筛选查询，否则可能将第201个之后的匹配当成“没有命中”。解析、文件和规则预算仍生效；命中满后继续检查余下定义/文件及最终规则和归属，后续未知或失败整次拒绝，不返回此前收集的片段。

| 字段 | 含义 |
|---|---|
| `source` / `policy` | `authorized_python_symbol_search` / `gitignore_subset_v1` |
| `content_trust` | `untrusted_project_content`；片段是外部资料，标记不证明提示注入防御 |
| `files` / `searched_files` | 实际成功解析和检索的Python文件元数据及数量，无定义的文件也计入 |
| `examined_symbols` / `matched_symbols` | 本轮观察到的定义/匹配数量，匹配数含输出上限之外的命中，不推算项目总数 |
| `matches[].symbol` | 原符号的相对路径、名称、类型、完整行范围、定义行与整文件SHA-256 |
| `matches[].matched_by` | `name` 或 `qualified_name`，明确匹配依据 |
| `matches[].snippet` | 有界纯文本、实际起止行列、片段 `truncated` 与 `incomplete_reasons` |
| 顶层 `truncated` / `incomplete_reasons` | 搜索覆盖不足；合并原扫描原因与 `match_budget`，不因片段裁剪而设为不完整 |

沿用 `workspace_id`、`task_id`、`parser`、扫描/候选读取与排除计数；非Python候选也占读取预算。最多返回20个匹配，观察到第21个才标记 `match_budget`。结果按相对路径/定义行排序，但选入子集遵循实际限量扫描顺序，不保证全项目排序优先。空匹配只能描述本轮支持范围；有覆盖不足时不能声称整个项目没有该符号。

每段最多20行、1000个Unicode码点、2048 UTF-8字节。默认包含装饰器与定义体的前缀；装饰器前部会耗尽任一片段预算时，从定义行开始并记录 `decorator_budget`。结束处分别记录 `line_budget`、`character_budget`、`byte_budget`，字节裁剪不保留半个UTF-8码点。片段归一化为LF，去掉裁剪末端的换行；起止行列均1起始且两端包含，末列按实际末行的Unicode码点计算，不能当UTF-16下标或AST字节列。末行被裁剪时不冒称返回了该行全部正文，完整符号范围独立保留。

片段与符号/文件摘要来自同次正文；SHA-256仍指向整文件原始字节，不是归一化片段。元数据不暴露宿主绑定根，源码本身含路径或指令时仍只是用户资料。当前没有浏览器、模型或Agent工具消费者，不把JSON输出当作已验证的纯文本UI或抗提示注入能力。

完整JSON另限64 KiB UTF-8，包含文本转义、查询、来源及覆盖字段。超限422 `python_symbol_search_result_too_large`；非法查询422 `invalid_python_symbol_query`；未知异常500 `python_symbol_search_failed`；解析及共享I/O错误沿用上面的固定映射。错误不反射查询、源码、宿主路径，不伪装为空匹配。查询验证先于扫描，但既有本机身份依赖仍可能执行幂等初始化。

最终复核不是数据库/文件系统原子快照，无法识别所有短暂变化或绑定变更后恢复；解析仍在API进程内，无硬超时或独立隔离。旧查询结果、片段与摘要都不是后续访问、写入或发送给模型的许可。

## Python 代码分块

当前仅提供内部服务 `build_python_code_chunks(*, user_id: int, workspace_id: str, task_id: str)`，位于[分块实现](../apps/api/app/services/workspace/files/python_chunks.py)；没有新增HTTP、工具、浏览器、模型、Embedding或索引写入入口。身份与任务来自可信调用上下文；分块不是文件访问许可，调用时仍执行当前授权扫描。策略 `python_innermost_definitions_v1` 只支持函数、异步函数及类的定义行区间；模块导入、顶层调用、定义外注释等不生成分块，由 `excluded_module_lines` 统计，不能把“分块完整”解释为“整个文件正文完整”。

独立的内存分块Embedding生成服务、配置和发送边界见[代码Embedding协议](code-embeddings.md)，尚未装配到扫描/HTTP/工具链路，不改变本分块服务的只读行为。

定义范围内的每个源码行归属包含该行的最内层定义。类头、属性和方法间隙归类定义；方法体归方法；嵌套函数体归嵌套函数。父定义的间隙保留为独立连续区间，不能跨过子定义拼接成一个不连续片段；装饰器包含在所属定义中。各区间不重叠、不重复正文，也不采用滑动窗口重叠。行范围未知或交叉时拒绝。分块是文本资料，拆开后的类头或函数体片段不保证能独立执行。

复用本轮过滤后的正文及内部AST消费者，200项符号清单输出限制不会截断分块候选；输入/词法/AST预算仍生效。每个连续区间优先在完整物理行后拆分；单行过长时按完整Unicode码点继续拆分，保留余下字符，不把裁剪后的前缀当作完整分块。CRLF/CR归一化为LF，字符串里的Unicode分隔符保留原样。行末LF也保留，因此按源码顺序拼接全部分块可恢复支持区间的归一化正文。

| 字段 | 含义 |
|---|---|
| `source` / `strategy` | `authorized_python_code_chunks` / `python_innermost_definitions_v1` |
| `files` / `parsed_files` / `examined_symbols` | 实际解析的Python文件元数据、文件数量及全部观察到的定义数，无定义文件也计入 |
| `generated_chunks` | 本轮支持区间生成的分块数，含返回上限以外的分块，不推算全项目总数 |
| `definition_lines` / `excluded_module_lines` | 解析文件中归属定义的去重物理行数、定义范围外的物理行数 |
| `chunks[].symbol` | 词法定义名称/类型、完整定义行范围与同次整文件原始SHA-256 |
| `chunks[].text` / `text_sha256` | LF归一化的连续正文及其独立SHA-256，不能用此摘要代替整文件版本 |
| `start_line` / `start_column` / `end_line` / `end_column` | 1起始Unicode码点坐标，起点包含、终点不包含；末尾LF的终点是下一行第1列 |
| `line_count` | 分块实际覆盖的物理行数，不把末尾LF之后的空终点再算一行 |
| `part_index` / `part_count` | 当前定义的第几片及本次生成的总片数；输出覆盖不足时总数仍含未返回分片 |
| `split_reasons` | 当前定义因子定义边界或预算拆分的原因，汇总为 `nested_definition` / `line_budget` / `character_budget` / `byte_budget` |
| `chunk_id` | 策略、相对路径、整文件版本、词法定义/定义行、实际坐标与正文摘要的确定性SHA-256 |

分块坐标采用半开区间，检索片段的末行/末列则包含在片段内，消费者必须按各自契约使用。空正文不生成分块。相同名称/正文在不同文件或位置的ID不同；整文件原始版本或策略改变时ID也改变，即使当前分块正文相同。ID仅用于当前版本关联，尚无持久索引、失效处理或访问权含义。

每片最多40个物理行、2000个Unicode码点及4096 UTF-8字节；这些不是模型Token预算。正常拆分不标记覆盖不足，单片也可能因子定义范围被移交而带 `nested_definition`。全请求最多返回20片，实际生成第21片才设置顶层 `truncated` 和 `chunk_budget`，与原扫描覆盖原因合并。输出满后仍解析、拆分、计数并复核规则/归属，后续未知或失败整次拒绝。选入子集沿用扫描/源码顺序，最终按相对路径和坐标排序，不保证全项目按路径取前20片。

完整JSON（含转义、来源、计数和覆盖标记）另限128 KiB UTF-8，超限抛出静态业务错误 `python_chunks_result_too_large`；不能切JSON、删来源或伪装为空结果。定义区间异常为 `python_chunk_invalid`；无法容纳完整码点为 `python_chunk_budget_exceeded`（固定正常预算可容纳任一Unicode码点）。解析及共享I/O/授权错误沿用既有服务错误，本课没有新增HTTP状态映射。

Session在目录/文件I/O前结束，分块只使用同次内存正文，不重新打开旧清单路径，也不执行源码、提交数据库事务或写索引。正文标记 `untrusted_project_content`，不能当作提示注入防御。最终复核与整文件摘要不是原子快照或当前版本保证；解析仍在当前进程内，无独立隔离或硬超时。

## 验证入口

- [忽略语义测试](../apps/api/tests/workspace/files/test_code_ignore.py)：支持/拒绝矩阵、规则预算、嵌套顺序，使用自建临时仓库与固定 Git 配置做语义对照；不执行用户仓库配置。
- [扫描服务测试](../apps/api/tests/workspace/files/test_code_inventory.py)：真实临时文件、有限枚举遗漏规则、硬排除、内容/类型、预算、规则变化与失败拒绝。
- [已注册 API 测试](../apps/api/tests/workspace/files/test_code_inventory_api.py)：真实本机身份、共享隔离 PostgreSQL、绑定/归属变化、无跟随读取、错误脱敏、Session关闭、文件与业务状态不变。

在 `apps/api` 使用 [环境说明的隔离测试配置](../ENVIRONMENT.md#5-定向测试与静态检查)，执行：

```bash
../../.venv/bin/python -m pytest -q tests/workspace/files/test_code_ignore.py tests/workspace/files/test_code_inventory.py tests/workspace/files/test_code_inventory_api.py
```

Python专项见[解析/扫描服务测试](../apps/api/tests/workspace/files/test_python_symbols.py)与[已注册API测试](../apps/api/tests/workspace/files/test_python_symbols_api.py)，沿用同一隔离配置：

```bash
../../.venv/bin/python -m pytest -q tests/workspace/files/test_python_symbols.py tests/workspace/files/test_python_symbols_api.py
```

符号检索专项见[服务测试](../apps/api/tests/workspace/files/test_python_symbol_search.py)与[已注册API测试](../apps/api/tests/workspace/files/test_python_symbol_search_api.py)，使用同一隔离配置：

```bash
../../.venv/bin/python -m pytest -q tests/workspace/files/test_python_symbol_search.py tests/workspace/files/test_python_symbol_search_api.py
```

分块专项见[内容/预算服务测试](../apps/api/tests/workspace/files/test_python_chunks.py)与[真实归属/只读事务测试](../apps/api/tests/workspace/files/test_python_chunks_authorization.py)，后者复用隔离PostgreSQL和临时源码目录验证服务授权：

```bash
../../.venv/bin/python -m pytest -q tests/workspace/files/test_python_chunks.py tests/workspace/files/test_python_chunks_authorization.py
```

本次分块仅新增模块，共享AST、扫描和HTTP实现未改动。后续修改共享AST消费者或HTTP响应处理时，再定向回归原符号位置、清单截断/异常及对应已注册API。无前端或跨端消费者改动，不作为浏览器、HTTP或模型链路验收；Windows 未实机验证。
