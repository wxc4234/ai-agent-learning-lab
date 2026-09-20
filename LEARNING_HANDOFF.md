# AI Agent 学习交接

更新时间：2026-09-20（Asia/Shanghai），第 5 周 Docker attach 输出帧解析已完成；下一课为 attach 异步读取与收尾。正式进度仍为 4/12。

这是新会话的唯一动态进度入口，长期规则见自动加载的 AGENTS.md。详细历史与验证记录按需查看，不再整篇加载计划或课程大纲。

## Runtime 目录整理（2026-09-20）

按用户要求，将29个运行时服务模块分入 `agent/`、`execution/`、`command/`、`sandbox/`、`docker/`，31个对应测试同步分组，导入、浏览器夹具与环境命令已更新；导航见 `docs/project-structure.md`。业务逻辑保持原样，下一课仍为 attach 异步读取与收尾，相关实现放 `services/runtime/docker/`。本次是工程整理，不推进课程或延续核心代写授权；本轮整理随代码与配套文档统一归档至 main，提交及远端同步以 Git 记录为准。验证结果见 `ENVIRONMENT.md` 末尾。

## 2026-09-20 当日收尾

今天停止学习，本次将累计第5周改动统一归档到主仓库main：Workspace只读文件/目录/单文件搜索与可信工具上下文、local聊天接入及PC闭环；命令契约/输出缓冲/环境；Sandbox策略、创建/核对/清理/停止/启动/退出结果；Docker attach帧解析及对应测试、验收记录、题库。

各课定向验收结果见下方及ENVIRONMENT.md，本次收尾不重复全量回归。下方“未提交推送”均为各课完成时的历史状态，本次统一提交归档；提交与远端同步以Git记录为准。正式进度仍4/12，第5周进行中。下一会话从“唯一下一课：attach异步读取与收尾”开始，今天不继续上课。用户的逐课代写授权不自动延续，恢复核心由学习者实现、完成后教练补测试；只回归新增及直接影响部分。

## 2026-09-20 接续

- attach帧解析课完成：docker_attach_parser.py核心与参考一致，无需修改；新增91条纯字节专项通过（0.09s），定向Ruff/diff check通过。覆盖全单切分/双切分、固定种子随机分块、通道交错/空帧/跨帧UTF-8、独立捕获限额与后续帧、1MiB帧边界、非法头/通道/保留字节/长度、半帧EOF、失败保持、误用不消费、完成幂等与实例隔离。无Docker/网络/数据库/浏览器/全量回归，未提交推送。

- 退出结果课完成（本课用户授权教练实现）：新增sandbox_exit.py，严格身份及一致exited/Pid=0后解析ExitCode/OOMKilled/Error，返回不可变结果；仅退出码0且无OOM/daemon错误时succeeded，不从137推断OOM或从143推断取消。原始daemon错误只保留布尔标记，不伪造输出/耗时。新增75条纯解析通过（0.09s），3条真实Docker验证0/7/主动137退出，均非OOM，临时资源已清理。Ruff/diff check通过，仅本课专项，无全量回归，未提交推送；代写授权仅本课。

- 容器启动课完成（本课用户授权教练实现）：新增sandbox_start.py及完整ID start适配。冻结原请求副本，先按同一响应核对身份/执行/隔离/created状态，再启动并重新查询running或快速exited。尝试start后的异常/取消衔接停止核对，shield收尾抵抗重复取消；错误/取消携带token/ID和stop_confirmed，不删除、不重试start。新增30条常规，直接相关客户端33条共63条通过（0.91s）；4条真实Docker通过（1.23s），覆盖running/快速exited/真实启动后人工响应丢失/取消后停止，临时资源无残留。定向Ruff/diff check通过；无数据库/浏览器/全量回归，未提交推送，本课代写授权不延续。

- 容器停止课完成（用户本课授权教练直接实现）：抽出read_sandbox_identity复用严格归属解析，保留原created检查；新增sandbox_stop.py及按完整ID inspect/stop适配。只接受一致created/running/exited状态，异常中间态拒绝；先授权再stop(SIGTERM/2秒)，重新查询非运行/Pid=0才确认，不自动删除或重试。新增53条常规，连同身份95/客户端33共181条通过（0.97s）；1条真实Docker专项通过（2.98s），父子忽略SIGTERM且子进程独立会话，daemon报告exited/Pid=0并拒绝exec，临时容器清理无残留。定向Ruff/diff check通过，无数据库/浏览器/全量回归，未提交推送；代写授权仅本课。

- 隔离与资源配置复核课完成：sandbox_isolation_policy.py核心与参考一致，无需修改。新增218条纯校验通过（0.16s），1条真实Docker专项通过（0.22s），真实created容器符合当前HostConfig/挂载策略；仅在响应副本改Privileged验证拒绝。临时容器已由清理服务删除并查询无残留，定向Ruff/diff check通过。仅新增测试，无启动/数据库/浏览器/全量回归，未提交推送；下一课先补停止与停止确认再推进执行生命周期。

- 执行配置复核课完成：sandbox_execution_policy.py核心与参考一致，仅给两处隐式字符串拼接加括号以满足Ruff（参考格式遗漏）。新增71条纯校验通过（0.08s），1条真实Docker专项通过（0.22s），复核未启动容器实际用户/入口/Cmd/Env/交互标志，篡改仅发生在响应副本；临时容器已按既有清理服务删除并确认无残留。定向Ruff/diff check通过；本课仅新增测试，无全量回归。网络/挂载/权限/资源配置尚未复核，不接启动，未提交推送。此前candidate_id类型收窄修复保留，相关145条测试已在修复回合通过，未重复运行。

- 显式清理课完成（本课用户授权教练直接实现）：新增sandbox_cleanup.py及Docker非强制rm/删除后缺失查询适配。按原上下文/完整ID重新核对created身份，核对删除回执并成功查询缺失才返回完成快照；失败保留token/ID且不重试，取消传播。新增41条常规专项，连同客户端33条共74条通过（0.90s）；2条真实Docker专项通过（0.48s），正常删除和删除生效后模拟响应丢失均验证，错ID不删、重复请求不再次rm，临时容器无残留。定向Ruff/diff check通过，无启动/数据库/浏览器/全量回归，未提交推送；本课代写授权不延续至下一课。

- 只读核对课完成：sandbox_reconciliation.py及身份恢复函数核心与参考一致，仅补sandbox_identity.py函数间空行和末尾换行。新增50条常规专项，连同直接相关身份解析95条共145条通过（0.13s）；1条真实Docker专项通过（0.27s）。验证未知ID发现、已知ID严格约束、错ID拒绝后目标仍为created、删除后查询仍保留未确认；临时容器按确认的完整ID清理，查询无残留。定向Ruff/diff check通过；无启动、数据库、浏览器或全量回归，未提交推送。

- Sandbox创建装配课完成：sandbox_creation.py及create适配核心与参考一致；仅补docker_client.py函数间空行和末尾换行。新增33条常规专项、2条显式Docker集成；常规连同直接相关客户端33条共66条通过（0.89s），真实Docker2条通过（0.27s）。验证正常创建与创建成功后模拟响应丢失仍保留容器且不重试，均确认created身份后按完整ID清理并查询无残留。定向Ruff/diff check通过；无容器启动、数据库、浏览器或全量回归，未提交推送。

- 有界Docker客户端课完成：docker_client.py核心与参考一致，仅补两处有原因的BLE001豁免（原参考静态检查遗漏），不改变行为。新增33条专项，连同直接相关双路捕获12条共45条通过（0.87s，-W error）；定向Ruff/diff check通过。覆盖固定程序/目标/环境、参数拒绝、失败脱敏、输出限额、启动及读取期间超时/取消、重复取消、kill竞态与reap、捕获失败；其中5条使用真实Python子进程/OS管道。只读Docker版本29.7.2及随机不存在容器inspect验证通过，未创建容器。无数据库/浏览器或全量回归，未提交推送。

- 创建响应/身份确认前置课完成：sandbox_identity.py核心与参考一致，无需修改。新增test_sandbox_identity.py 95条通过（0.07s，-W error），两文件Ruff及diff check通过。覆盖完整ID及行结束符、异常/超长/深嵌套JSON、重复字段/非标准数值拒绝、身份串用、必需字段/类型、镜像/标签/created状态及严格PID、额外字段兼容、不可变快照和安全错误。仅本课纯解析专项，无Docker/数据库/浏览器/全量回归。实际创建调用尚未实现，未提交推送。

- Sandbox参数构造课完成：sandbox_spec.py核心与参考一致，仅给两处tmpfs隐式字符串拼接加括号以满足Ruff（参考格式遗漏）。新增test_sandbox_spec.py 44条通过（0.05s，-W error），两文件Ruff及diff check通过。覆盖固定Docker策略/环境分层、参数原样保留、程序位置防env赋值、token/镜像拒绝、未挂载目录拒绝、请求重校验与不可变快照；仅本课纯参数专项，无Docker/数据库/浏览器或全量回归。未提交推送。

- Sandbox最小验证完成：infra/sandbox/compose.yaml与参考一致，无需修改。发现本机尚无指定镜像/练习容器，教练拉取python:3.12-slim-bookworm并按实际摘要固定；新增scripts/verify_sandbox.py，在随机Compose项目完成3组真实验收：权限/文件/网络/资源/主进程环境、忽略SIGTERM且独立会话的子孙进程随容器停止、重启tmpfs清空及宿主哨兵不变。临时容器清理完成，Ruff/diff check通过。网络额外隧道接口均DOWN，修正测试“仅lo”的过强假设后外连确实拒绝。仅本课专项，无数据库/浏览器/全量回归；镜像保留本地，未提交推送。

- 命令环境白名单课完成：command_environment.py核心与参考一致，无需修改。新增test_command_environment.py 62条通过（0.05s，-W error），两文件Ruff及diff check通过。覆盖精确11字段白名单、宿主敏感/注入变量不继承且不读取os.environ、两目录严格类型及POSIX路径边界、输入保留、额外覆盖拒绝、独立返回字典与无文件系统调用。仅该纯函数专项，无外部命令/数据库/浏览器或全量回归。环境白名单不是Sandbox；未提交推送。

- 双路输出课完成：command_capture.py核心与参考一致，无需修改。新增test_command_capture.py 12条通过（0.05s，-W error），两文件Ruff及diff check通过；测试显式导入builtins.ExceptionGroup以适配当前Ruff目标版本。覆盖真实内存流/独立输出限额、成对契约、同流拒绝、双方启动屏障、单路先EOF、双向故障取消并等待异步收尾、整体取消等待两路、同时失败异常组，终态无遗留读取任务。仅本课专项，无外部命令/真实管道/数据库/浏览器或全量回归。未提交推送。

- 单路异步输出课完成：command_stream.py核心与参考一致，无需修改。新增test_command_stream.py 31条通过（0.06s，-W error），两文件Ruff及diff check通过；仅本课专项，内存StreamReader/受控读取器验证，无外部命令、真实管道、数据库、浏览器或全量回归。覆盖EOF/短块/大输出、额度耗尽继续读取、无效策略在读取前拒绝、读取契约检查、异常原样传播、等待及连续立即返回时取消、流不被主动关闭、并发实例隔离。未提交推送。

- 输出缓冲课完成：command_output.py核心与参考一致，仅按Ruff排序__slots__（参考代码的格式遗漏）。新增test_command_output.py 41条通过（0.05s，-W error），两文件Ruff及diff check通过；仅本课纯内存专项，无命令进程/管道/数据库/浏览器或全量回归。覆盖硬上限、精确边界、持续超限保存量不增长、所有切分位置UTF-8、非法及末尾不完整字节、双截断、完成幂等及拒绝写入、独立两路与CommandResult组装。未提交推送。

- 命令契约课完成：command_contracts.py核心与参考一致，仅清理末尾空白。新增test_command_contracts.py 120条通过（0.10s，-W error），两文件Ruff及diff check通过；仅该单文件专项，无数据库/浏览器/命令进程或全量回归。验证argv/路径语法边界、策略字段拒绝、非零及信号退出码、超时/取消、启动失败一致性、独立输出上限/截断及JSON往返。格式合法不代表执行授权，资源常量尚未接执行器，Sandbox和子进程停止证据待实现。未提交推送。

- 只读工具链PC闭环4组通过：键盘发送→真实BFF/API/隔离PostgreSQL/临时文件→列目录、搜索定位、读取并回答；刷新答案与3条工具结果事件恢复且无新聊天POST；越界读取和未绑定目录安全失败。1366/1920成功页及两种失败页截图已视查，Ruff/Node语法/diff check通过。仅新增readonly-tools.mjs、readonly_model.py和隔离模型分支，未改业务代码；模型决策模拟，真实模型效果未验收。只跑本课浏览器专项，无全量回归；临时资源清理完成，未提交推送。

- 搜索工具适配与注册完成：严格参数、可信上下文、安全错误及定位/截断JSON保留已验收。检查发现新增搜索条目覆盖了原get_current_time注册，教练恢复原有条目；搜索核心无需其他修改。新增45条工具测试，与直接相关适配/注册回归共122条通过（0.91s，-W error），定向Ruff通过。受控模型验证列目录→搜索→行号回答及失败路径，底层服务模拟；未跑数据库、文件系统、浏览器、真实模型或全量回归。未提交推送。

- 学习者完成workspace_search.py，核心与参考一致，无需修改。新增test_workspace_search.py 43条通过（1.07s，-W error），本课两文件Ruff及diff check通过；仅本课单文件专项，无读取/聊天/Runtime全量回归。覆盖查询规则、字面量/大小写/每行首次匹配、Unicode列号、换行、50/51行与片段预算、错误原样传播，以及3条隔离PostgreSQL+真实文件成功/跨用户/非法UTF-8集成。无浏览器/模型调用，未提交推送。
- 搜索最多返回50个匹配行，每行片段最多200字符；字符列号从1开始，片段截断与匹配行截断独立，不提供总匹配数或JSON/Token精确预算。服务复用256KiB读取边界，目前尚未注册搜索工具。

- 学习者完成list_directory工具适配、目录错误白名单及requires_context注册，核心与参考一致；教练仅补errors.py类间空行、同步旧工具可见列表断言。新增test_list_directory_tool.py 31条，与直接相关读取适配/注册回归共77条通过（0.71s，-W error），定向Ruff及diff check通过。未运行数据库/文件系统/浏览器或聊天全量，模型与底层服务模拟。
- 受控模型实际根据目录Observation中的名称发起第二次读取，验证列目录→读文件→回答；默认“.”、额外能力参数拒绝、truncated保留、11类错误映射、未知异常脱敏及无上下文强行调用拒绝已覆盖。当前local流式聊天自动可见两种项目工具，普通/chat与无上下文演示入口不扩展能力。未提交推送。

- 学习者完成workspace_listing.py，核心与参考一致，无需修改。教练新增test_workspace_listing.py 26条通过（1.11s，-W error）；仅本课单文件专项和两文件Ruff，diff check通过。真实目录/链接/FIFO、200/201及250条限量迭代、子项消失/目录变化、无跟随打开、错误脱敏、资源清理及隔离PostgreSQL授权已覆盖。未跑读取/Runtime/聊天/全量回归，无浏览器或模型调用；未提交推送。
- 目录服务只返回单层有限子集，不暴露链接目标；truncated不是稳定分页或全局排序前缀，不提供原子快照/完整Sandbox。下一课接目录工具注册与安全错误，当前模型仍只有文件读取而无目录枚举工具。

- 学习者完成stream_chat_reply本地上下文装配，核心与参考一致，无需修改。新增test_chat_tool_context.py 7条通过（0.42s）；受影响流式回归9条（0.08s）、取消/断线生命周期4条（1.05s）通过，共20条分组验证。定向Ruff及diff check通过。只回归直接影响部分，无全量。
- 教练同步旧流式Runtime替身的tool_context签名，并把旧生命周期夹具新增上下文服务的SessionLocal接入隔离库；两处配套缺失曾分别造成RUN_ERROR和等待保存阶段超时，修复后通过。新增真实链路从隔离PostgreSQL任务上下文到临时文件读取、工具事件、消息和Run终态持久化，模型模拟；未运行浏览器或真实模型。普通/chat仍直接文本请求，账号流式不装配文件上下文；未提交推送。

- 学习者完成read_text_file工具适配、SafeToolExecutionError固定文案、注册及按上下文过滤模型工具列表；核心与参考一致，教练仅补新文件末尾换行并更新旧注册列表测试断言。新增test_read_file_tool.py 37条，与直接相关工具/API/模型适配/上下文派发回归合计83条通过（0.93s，-W error）；定向Ruff、diff check通过，无数据库/文件系统/浏览器/真实模型回归。
- 模拟模型完整往返验证成功JSON、安全错误与未知错误Observation、无上下文强行调用拒绝；模型请求不携带上下文身份，默认TOOLS仅时间/面积。当前聊天入口仍未传上下文，因此用户聊天尚不能读取项目文件；下一课装配。未提交推送。

- 学习者完成ToolDefinition.requires_context、保留参数防覆盖、execute二次检查及Runtime前置拒绝/显式透传；核心与参考一致，教练仅补类间空行。新增test_tool_context_dispatch.py 20条，与23条直接相关工具/线程/超时/取消/事件回归合计43条通过（1.17s，-W error）；本课三文件Ruff及diff check通过。未连接数据库、运行浏览器或调用模型，无领域全量回归。
- 缺失上下文沿用tool_execution_failed，固定details=tool_context_required且duration_ms=None；参数校验仍先执行，直接execute也拒绝缺失上下文。流式/非流式、真实跟踪线程和并发隔离已覆盖。上下文尚未从聊天入口装配，文件工具尚未注册；未提交推送。

- 学习者完成 tools/context.py 与 services/runtime/agent/tool_execution_context.py，核心与参考一致；教练仅补末尾换行。新增test_tool_execution_context.py共24条通过（1.20s，-W error），本课三文件Ruff及diff check通过。真实隔离PostgreSQL验证两模式同等归属条件、不同会话/项目定位、无Task拒绝、未绑定目录允许、仅SELECT无commit、SQL错误与Session关闭；对象不可变及不接受独立资源参数也已覆盖。
- 本轮仅运行上述单个测试文件，无Runtime/账号/路径/读取或全量回归；独立测试资源自动清理，未操作开发业务表/迁移，未调用模型。上下文工厂已完成，Runtime透传和文件工具注册仍未接入。未提交、未推送。

- 受限文本读取课：学习者完成 workspace_file.py，核心与参考一致，教练仅补末尾换行；新增35条读取测试，与路径边界63条合计98条通过（1.69s，-W error），本课两文件Ruff及diff check通过。真实描述符覆盖普通/空/中文/上限文件、非法文本、特殊文件、打开阶段替换、读取期间修改、有界读取与异常关闭；两条隔离PostgreSQL验证真实授权及Session先关闭。未运行领域/后端全量、浏览器或模型，未操作开发业务表/迁移；未提交推送。
- 后续回归遵循用户最新要求：只运行新增及直接受影响测试，不默认按整个领域或后端全量扩展；确有跨模块影响或异常证据才说明原因后扩大。

- 第5周路径边界课：学习者完成 workspace_path.py，核心与参考一致；教练只补末尾换行。新增63条专项通过（1.54s），Workspace/Task/local相关回归659条通过（46.18s，-W error），全量后端Ruff及diff check通过。覆盖跨平台输入规则、真实链接逃逸、隔离PostgreSQL授权先行、只读查询、会话先关闭及异常清理。
- 首次专项53条通过后因沙箱阻止本机数据库连接而中断；放行测试连接后重跑通过，独立测试库/schema已自动清理。未操作开发业务表/迁移，未调用模型或运行浏览器，Windows未实机验证。当前仅路径快照，不是读取权限、TOCTOU防护或Sandbox。下一课见“唯一下一课”；本课改动未提交/推送。

- 最新收尾：按用户授权完成有历史任务删除，以及本机异常退出恢复的服务/HTTP/BFF/UI。后端最终全量1411条通过（105.58s），Workspace718/聊天86、类型检查、ESLint/Ruff通过；真实子进程kill、事务失败回滚、旧数据迁移与浏览器恢复闭环均已覆盖。详情见 ENVIRONMENT.md 最后章节。
- 第4周按原生本机、进程内工具线程范围验收完成，正式4/12（33.3%）。旧版无执行者身份、外机或权限不足的记录保守拒绝恢复；不按TTL清理，不自动续跑工具。第5周Shell须扩展子进程树停止证明，第7周再做checkpoint续跑，第9周做跨进程预算/交付。
- 开发库已显式迁移至3da097b362fa，alembic check无差异；未删除开发业务数据。本次按用户要求将累计源码、测试、迁移与文档统一提交并推送 origin/main；推送后在同一主仓库 main 创建新任务，继续第5周路径边界课。以下为本日各课当时的阶段记录，不代表当前提交状态。

- 学习者完成 ConversationExecutionStatusResponse 及 GET /sessions/{session_id}/execution，核心与参考一致；教练仅补定义之间空行。复用 CurrentUser 和 ChatRoute，在线程中执行同步查询，响应只含公开三字段，成功与错误均 no-store。
- 新增 tests/chat/test_conversation_execution_api.py 共 13 条真实应用/隔离 PostgreSQL 专项，随查询服务、本地访问/任务边界及聊天占用生命周期共 90 条通过（7.70s，-W error）；Ruff/diff check 通过。覆盖空闲/占用/释放、四类授权拒绝、本地访问拦截、身份伪造、线程执行、数据库故障脱敏、Run 状态独立和业务数据不变。
- 首次测试因 PostgreSQL 未监听而停在夹具初始化；确认 Docker 并启动 Compose PostgreSQL/Redis 后健康，重跑通过。未修改开发业务表或迁移；本课未运行前端/浏览器，未调用模型。
- 学习者完成占用查询解析器、代理及 Next.js GET 路由，核心与参考一致；教练仅补末尾换行。新增 conversation-execution-route.test.ts 60 条通过，Workspace 全量 672 条通过（1.79s）；类型检查、ESLint/diff check 通过。上游 fetch 模拟，验证服务端凭证隔离、公开字段重建、错误白名单、无重试及请求/正文阶段取消与超时。
- 学习者完成 ConversationExecutionPanel 和右侧详情接入，核心无需修改，教练仅补末尾换行。新增 8 条真实 refresh 函数测试，Workspace 680 条、聊天状态85条、类型/ESLint/diff check 通过。
- 新增 conversation-execution.mjs 浏览器 5 组验收通过：真实 BFF/API/PostgreSQL 占用与取消释放，键盘查询、错误与契约失败重试、切换任务和关闭详情的迟到响应隔离。1366/1920 PC 截图已视查；隔离服务及数据库已清理。模型模拟，不宣称验证强制停机恢复。
- 学习者完成 ExecutionBudget，核心无需修改。新增23条专项，随 ExecutionThreads 共35条通过（0.07s，-W error），Ruff/diff check通过。验证20个竞争者仅3个获准、满额拒绝不误归还、异常/取消归还、内层收尾期间保留容量、重复退出不误释放、实例独立及跨循环拒绝。未运行数据库/浏览器，尚未接聊天入口。
- 学习者完成配置、请求依赖与安全错误映射；检查发现 main.py 漏了启动装配，教练补共享预算 lifespan，并更新测试夹具与 .env.example。新增配置/生命周期17条、真实ASGI容量6条；加强既有数据库提交/工具断线测试的预算保持断言。后端全量1371条通过（111.88s，-W error）。
- 认证/BFF207条、聊天状态86条、类型/ESLint/Ruff通过。不同会话共享预算，满额503且无执行副作用，预算包住会话占用和请求收尾；本地主线先授权，账号扩展保留原会话准备行为。
- 容量1的真实PC浏览器2组通过：不同会话收到503，取消后第二会话恢复执行与刷新历史；1366/1920截图已视查，临时服务及隔离库清理完成。
- 学习者完成 historical-run-summary.ts 与历史详情卡片接入，核心与参考一致，教练仅补末尾换行。新增23条摘要专项，Workspace703/聊天86、类型/ESLint/diff check通过；未知指标不补零，金额不转number，仅匹配唯一终态恢复摘要。
- 历史摘要PC浏览器4组通过：真实持久化摘要刷新/重选恢复；受控失败、缺失及冲突数据正确显示。1366/1920截图已视查，隔离服务/数据库已清理。未调用真实模型。
- 第4周范围核对已完成：复核当前删除/占用事务与外键、课程目标和既有验收证据，修正文档中摘要恢复及并发预算的过期待办。本轮仅改文档，未重跑功能测试；删除服务保护随后按用户授权直接实现；占用冲突现已按用户授权接通 API/BFF/UI。累计改动未提交、未推送。

## 2026-09-18 接续

- 检查运行链路后，将占用接入先拆出后台线程跟踪这一前置课：asyncio.to_thread 的等待取消或超时不代表线程结束，不能立即释放占用。
- 学习者完成 services/runtime/execution/execution_threads.py：每次执行独立跟踪任务，shield 隔离调用方取消，关闭后拒绝新工作，等待全部线程工作结束后再传播收尾期间的取消。核心与参考一致，教练仅补末尾换行。
- 教练新增 12 条真实线程专项；随既有 Agent Loop 回归共 28 条通过（0.82s，-W error），后端 app/tests Ruff 与 diff check 通过。未调用模型、数据库或浏览器，未修改运行入口。
- 学习者已完成 conversation_execution_scope.py，采用 AsyncGenerator[ExecutionThreads, None] 标注。核心无需修正，教练仅补换行及说明预期异常捕获的 Ruff 豁免。新增 17 条隔离 PostgreSQL 专项，Runtime 共 165 条通过（12.45s，-W error），Ruff/diff check 通过。验证获取期间取消、线程收尾、重复取消、AnyIO 取消域、提交确认丢失和精确释放。
- 学习者已完成 Agent Loop 的 execution_threads 可选参数、工具调用转交及非流式兼容函数透传，核心无需修正。新增 12 条工具接入测试，Runtime 共 177 条通过（13.70s，-W error）；Ruff/diff check 通过。两条真实 PostgreSQL 集成验证超时/取消后占用保留至工具线程结束。
- 本课用户明确授权教练直接实现：两个聊天入口共用 request 级依赖，覆盖响应发送、Run 创建、生成器与取消监听器关闭、后台数据库/工具线程排空、缓存失效及精确释放；API/BFF/UI 接通安全 409，保留账号扩展兼容。
- 新增 ASGI/PostgreSQL 专项 12 条通过，后端全量 1300 条通过（99.38s，-W error）；BFF 37、聊天状态 85、TypeScript/ESLint/Ruff 通过。旧状态测试夹具补 setCreationError，避免误把夹具缺依赖当成业务失败。PC 浏览器并发忙提示、取消后恢复及刷新历史两组通过，1366/1920 截图已视查，隔离服务及数据库已清理。
- 学习者完成 conversation_execution_query.py，核心与参考一致，教练仅补末尾换行。新增 12 条隔离 PostgreSQL 专项，Runtime 共 189 条通过（13.96s，-W error），Ruff/diff check 通过。覆盖占用获取/释放快照、先授权、旧占用与 Run 独立、仅 SELECT 且不读 token、不提交及查询异常传播。
- 下一课为会话执行占用状态查询 HTTP 接口。今日改动未提交或推送；上一轮 main 提交为 8c0fb84。此前聊天入口直接实现授权不扩展至后续课次。

## 2026-09-17 接续

- 学习者明确“完成了”后，已检查创建幂等事务服务。核心与参考一致；教练仅按 Ruff 合并等价嵌套 if、补文件末尾换行，并补测试和收尾文档。
- 已实现：先授权并锁项目、规范化标题和服务端 SHA-256、按用户/项目/键查询请求记录、同键重放/内容冲突/删除后拒绝重建、重新核对任务和会话归属、三条记录同事务提交。重放返回当前公开资料。
- 新增 37 条专项，随 Task/Workspace/local 相关回归共 545 条通过（41.45s，-W error）；后端全量 Ruff 与 diff check 通过。没有重复执行账号专项、前端或浏览器验收。
- 已启动 Docker Desktop，项目 PostgreSQL/Redis 健康；测试仅使用自动清理的随机独立库/schema，未迁移或修改开发业务表。详细命令与初次环境阻塞见 ENVIRONMENT.md。
- 本课 HTTP 接入核心与参考一致，无需修正。请求键暂时可选，严格正文校验；两种冲突返回安全 409，非法键返回 422，未知失败保持创建结果未确认。新增 37 条 HTTP 专项通过（3.83s），Task/Workspace/local 相关回归 582 条通过（42.15s，-W error）；Ruff/diff check 通过，隔离资源已清理。
- BFF 课按用户“这个你直接完成吧”授权由教练实现：严格校验并原样转发可选请求键，补 409/422 白名单，保留缺省/null 兼容和未确认语义，不生成键或自动重试。创建路由 92 条（新增 33）、Workspace 全量 612 条、TypeScript/ESLint/diff check 通过；上游 fetch 模拟，未运行浏览器/数据库。
- UI 课用户再次明确授权直接实现并要求解释。教练已接入工作台内存创建意图、首发请求键、冻结输入重试、冲突/删除拒绝及明确新意图；重试成功读历史，不自动发送消息。新增 4 组 PC 浏览器场景通过；提示修正后重跑关键恢复场景 1 组及既有首发/切换/列表找回/历史隔离 4 组通过，浅深色 PC 截图已视查。Workspace 612/聊天状态84、TypeScript/ESLint/diff check通过，临时服务及隔离数据库已清理。
- 本课学习者完成 ConversationExecutionSlot 模型与迁移，核心无需修正，只补迁移末尾换行；新增13条专项随迁移目录56条通过（7.42s），后端全量1205条通过（82.51s，-W error）；Ruff/diff check通过。开发库从0a7b64e039cd升级至1b8c75f140de，alembic check无结构差异。
- 本课学习者完成 conversation_execution_service.py，核心与参考一致，无需修正；教练新增 42 条专项，随 runtime/tasks/local 回归 508 条通过（36.91s，-W error），Ruff/diff check 通过。验证授权先行、短事务、真实锁等待、提交确认丢失及旧 token 不误释放；尚未接入聊天运行。
- 下一课是聊天执行生命周期中的占用接入；此前 UI 代写授权仅限对应课次，后续仍由学习者实现核心。继续在主仓库 main 学习，一课一个任务；本次按用户要求将当日累计源码、测试、迁移和对应文档统一提交并推送至 origin/main；正式进度仍为 3 / 12。

本次收尾范围：Task 创建幂等事务服务及 HTTP/BFF/UI 全链路、会话执行占用模型/迁移及获取/释放服务。验收证据已归档 ENVIRONMENT.md，课程范围与状态已同步 LEARNING_CURRICULUM.md、LEARNING_PLAN.md，重要考点已更新题库及索引。今天不继续接入运行生命周期；下次从“唯一下一课”开始。

## 当前目标与约束

- 正式进度：第 1～4 周完成，4 / 12（33.3%）；第 5 周进行中，已完成只读工具链与 Sandbox 生命周期基础、attach 帧解析。
- 主产品：本地优先 PC Coding Agent。Web UI/API/执行服务在用户电脑，模型由用户配置；无需产品注册登录。账号模式作为既有扩展保留，不主动推进访客系统或账号专项课程。
- 技术路线仍为 Next.js + FastAPI + PostgreSQL + Redis。域名用于展示、文档和分发；第 9 周保留独立受限样例的云部署学习。
- 用户要求在已检出 main 的主仓库学习。每次确认分支与工作区，不覆盖累计未提交改动。
- 一课一个可运行小任务，完整参考直接展示在对话中；已有文件只给完整改动段/函数，新文件才给整文件；四空格缩进，关键逻辑前说明原因和边界。
- 用户明确“完成了”后才检查实现、补测试和验收。测试和机械性配套由教练完成；口头答题不是课程门槛。
- 本轮用户直接授权了对话式侧栏/标题/历史、18px 样式和目录整理；后续课程恢复学习者编写核心实现，不能将这次授权当成永久代写授权。

## 当前界面与行为

- Codex 风格项目/任务侧栏，项目行有笔形新建入口和项目设置；目录选择及绑定放在设置中，不常驻项目 ID、时间和手填标题表单。
- 笔形入口只打开草稿，首次发送才创建 Task/Conversation；同任务后续消息使用同一 conversation_id。列表支持分页，选回任务读取真实历史。
- 第一轮完成后异步总结标题，失败保留首条消息摘录；后端重新授权并按旧标题条件更新，迟到结果不覆盖后来标题。
- 创建响应未确认时停止自动发送，可以从列表找回已创建的空任务；创建服务、HTTP、BFF 和 UI 已接通请求键；未确认时同键重试找回任务，读历史后等待明确发送。
- 空白态引导与输入框居中相邻，有消息后输入框固定底部；详情默认收起，可恢复三栏。左右布局开关独立于任务身份，收起导航不丢草稿。
- 选中任务和首次创建后同步 URL；刷新及新标签页打开链接自动恢复任务与历史，继续使用原会话。草稿清除任务参数；不持久化未发送输入，不建立任务切换的浏览器前进/后退历史，历史运行列表、时间线与有效终态摘要可重新读取；不自动续跑中断执行。
- 全页正文、控件、提示基线 18px，标题更大；左侧栏 320px。右侧详情默认 400px，支持 320～720px 拖动与方向键/Home/End 调宽、双击重置、localStorage 保存偏好，并根据窗口为中栏保留空间；当前运行与摘要改为分区/行式指标，数值不拆行。继续复用共享控件和主题。
- 任务删除：Provider 状态、ref 防重、精确详情确认、迟到结果隔离及 URL 清理已接通；本轮扩展到无占用且运行均结束的历史任务，并更新确认文案。教练按用户两张 Codex 截图调整任务行悬停删除图标、应用内确认框和项目菜单；保留 18px 字号。项目菜单仅接入新建任务、项目设置和刷新任务，不伪造归档/置顶/移除能力。
- Enter 发送，Shift+Enter 换行；输入法 composition/229 不误发。历史读取失败阻止发送，旧请求不会污染新任务。

## 已验收的后端与运行基线

- 第 1～3 周：FastAPI/模型调用、会话持久化、BFF NDJSON 透传、流式事件解析、六态聊天、工具注册/参数校验/Agent Loop、超时取消、最大步骤、run/event 时间线、指标与预算。具体边界见 docs/agent-ui-events.md 和 week-learning/week-03/REVIEW.md。
- 账号扩展：注册、登录、身份、退出与资源所有权。local 模式依赖稳定本机身份、Loopback Host、来源检查及服务端内部凭证；身份不替代资源授权。
- Workspace：创建、列表、原生目录选择和绑定/读取。选择目录不等于获得任意文件写入或命令执行权限。
- Task：模型、创建事务、HTTP/BFF、列表/分页/历史/标题。Task 与 Conversation 同事务创建；提交后响应失败可能返回错误但记录已存在，不能自动重发。
- Task 详情 HTTP：TaskDetailResponse、task_detail 和 GET /workspaces/{workspace_id}/tasks/{task_id}。返回项目资料及任务/会话公开标识；定位不依赖列表第一页，检查项目和会话归属，只读且不返回 ORM 对象。
- Task 详情 BFF：学习者完成同源 Task 详情 BFF、readTaskDetail 与 taskProxy 的 detail 分支。校验 URL 项目/任务与响应匹配、会话标识格式，重建公开字段；复用本地凭证、取消超时、错误脱敏和 no-store，兼容列表/messages/title。

- URL 恢复：学习者完成 task-url.ts、工作台 URL 恢复及恢复状态 UI。选中/创建后 replaceState 更新定位参数，刷新或复制链接通过详情恢复项目/任务，再由 TaskChat 读取历史；详情失败不回退成草稿，旧详情不得覆盖新选择，创建后保持组件 key。教练修正参考中 effect 同步 setState 的 lint 问题，补测试与隔离夹具。

- 空任务删除事务：学习者完成 task_deletion_service.py，仅删除没有消息、没有任何状态 Run 的空任务；按项目→任务→会话授权加锁，先删会话再删任务，同事务提交/回滚，保留项目与目录。核心无需修改，仅补末尾换行。现已接通 HTTP/BFF/UI；不代表已完成通用删除或运行生命周期收口。

- 本地会话保护：学习者收紧 conversation_repository.py：local 模式只允许已有 Task 会话，联查 Conversation/Task/Workspace 归属；创建 Run 前锁会话且不执行会话 INSERT，普通聊天在缓存/模型调用前重新检查。账号分支保留兼容。核心无需修正，仅补末尾换行。

- 最新完成：学习者实现空任务 DELETE HTTP，教练按本轮明确授权实现同源 DELETE BFF。成功 204 空正文；404 不可访问、409 已有历史；状态与错误码白名单脱敏，转发后超时/取消/异常提示删除结果未确认，不自动重试。该授权仅限本轮 BFF，后续核心课程仍由学习者实现。

- 任务运行历史查询：学习者完成 task_run_query.py 与两种响应结构；复用 owned_task 授权后按 Run ID 倒序读取 limit + 1 条，返回概要和下一页游标，不加载事件，不写入或提交。未知状态保留原值，结束时间为空则最终耗时为空。查询服务、HTTP 与 BFF 已验收，列表 UI 已接通（手动刷新，不订阅实时状态）。

## 当前目录入口

后端按“层级 → 领域”组织，目录导航见 docs/project-structure.md。不要再向 routers/services/repositories 根层平铺新模块。

- HTTP：apps/api/app/routers/workspace/workspace.py
- Task 服务：apps/api/app/services/tasks/task_service.py、task_workspace.py
- Workspace 服务：apps/api/app/services/workspace/
- 模型与运行：apps/api/app/services/model/、services/runtime/
- 仓储：apps/api/app/repositories/{auth,chat,workspace,runtime}/
- 响应结构：apps/api/app/schemas.py；ORM：apps/api/app/models.py
- Task 测试：apps/api/tests/tasks/，公共夹具仍为 apps/api/tests/conftest.py
- 前端详情代理基础：apps/web/src/app/api/_shared/task-proxy.ts
- 工作台状态：apps/web/src/features/workbench/workbench-session.tsx
- 浏览器夹具：apps/web/test/browser/run-isolated.py、chat_test_app.py

目录整理移动了 97 个源码/测试文件，同步 Python 导入、测试包路径、浏览器脚本、迁移测试相对路径和文档引用；没有旧路径转发壳。应用启动入口仍为 app.main:app，不需要数据库迁移。

## 最近验证

- 2026-09-16 Markdown 展示修复：接入 react-markdown 10.1.0 + remark-gfm 4.0.1，新增 MarkdownMessage，历史助手消息与流式回复统一解析，用户消息保留纯文本。覆盖标题/强调/列表/任务列表/引用/代码/表格，代码表格内部滚动；禁用原始 HTML，沿用 URL 校验，图片显示显式链接。浏览器 3 组通过（逐块模拟流、格式/安全/滚动、历史恢复/浅深色），截图已视查；TypeScript、ESLint、聊天84/Workspace579及 diff check 通过。模型输出模拟，隔离服务和测试库已清理，下一课不变。

- 2026-09-16 右侧栏：按用户截图修正摘要依赖屏幕断点导致的三列挤压，改为标签/数值行式布局，移除当前运行嵌套卡片。右栏默认 400px，拖动/方向键/Home/End/双击调宽，持久化偏好并限制中栏空间。浏览器 9 组通过（原 8 组 + 真实聊天摘要/调宽），用模拟指标复现 757 Token、¥0.00128420、1499 ms，320px 下无断行；拖动保留草稿与节点、刷新恢复宽度。1366px 窄/宽截图已视查；Workspace 579、聊天 84、TypeScript、ESLint 通过，临时资源已清理。

- 2026-09-16 启动迁移职责：按用户明确授权移除 API 启动 create_all，新增 check_database_ready，只读比较迁移 heads，不一致拒绝启动；迁移 env 支持显式连接，浏览器隔离环境先执行真实 upgrade。新增 7 条专项通过，后端全量 1101 条通过（79.95s，-W error），Ruff/diff check 通过。开发库仅只读检查版本并通过，未迁移或写入。

- 2026-09-16 历史详情 UI：按用户明确授权完成 TaskRunPanel、列表选择入口及独立历史详情状态。返回保留列表，重试/返回/任务切换清理旧请求，文本由 React 转义。浏览器原 5 组列表 + 新增 3 组详情共 8 组通过，1366/1920×900 截图已视查；Workspace 579 条、聊天状态 84 条、TypeScript、全量 ESLint 和 diff check 通过。真实空任务链路，其余分页/详情/故障模拟；临时服务和隔离数据库已清理，无真实模型调用。

- 2026-09-16 运行详情 BFF：用户明确授权直接完成 GET /api/runs/[runId]、run-detail-proxy.ts 与 run-detail-data.ts。新增 61 条、Workspace 全量 579 条及聊天状态 84 条通过，TypeScript、ESLint、夹具 Ruff 与 diff check 通过。复用流事件解析器，兼容持久化开始/取消事件，未知事件保留信封但 payload 置空；核对 Run ID、事件 ID 严格递增和时间/耗时，重建公开字段。测试直接调用路由、模拟上游，无浏览器/数据库/模型调用；隔离启动器已补复制新路由。

- 2026-09-16 运行历史 UI：学习者完成右侧概要列表、分页、刷新、失败重试及任务切换取消/旧回调隔离。教练恢复被替换掉的 RunSummaryCard 并补文件换行；隔离启动器补复制 runs 路由。新增浏览器 5 组全部通过（真实空任务 BFF/API，其余分页和故障响应模拟），1366/1920 PC 截图已视查；Workspace 518 条、聊天状态 84 条、TypeScript、ESLint、夹具 Ruff 和 diff check 通过。临时服务及独立数据库/schema 自动清理。

- 2026-09-16 运行列表 BFF：按用户本轮明确授权完成 task-run-data.ts、task-run-proxy.ts 和 runs/route.ts；新增 79 条路由专项、Workspace 全量 518 条通过，TypeScript、全量 ESLint 与 diff check 通过。覆盖内部凭证隔离、参数重复/越界、项目/任务匹配、排序/游标、公开字段重建、错误脱敏及请求/正文/JSON 完成阶段取消超时。直接调用真实 GET 路由，上游 fetch 模拟；未运行浏览器、数据库或模型。授权仅限本课 BFF，后续核心仍由学习者实现。

- 2026-09-16 运行列表 HTTP：核心实现与参考一致，无需修改。新增 test_task_run_api.py 42 条通过（4.53s）；Task/runtime/local/workspace 共 602 条通过（44.08s），-W error；全量后端 Ruff 与 diff check 通过。覆盖真实分页、跨会话隔离、空任务授权、非法参数、访问边界、SQL/响应异常脱敏与 no-store；隔离库/schema 自动清理。未接 BFF/UI，未运行浏览器或模型。

- 2026-09-16 运行列表查询：新增 test_task_run_query.py，44 条隔离 PostgreSQL 专项通过（2.67s），覆盖分页、归属、状态时间、无事件读取/写入/commit、真实 SQL 错误与 Session 清理。核心无需修正，仅补文件末尾换行。Task/runtime/local/workspace 共 560 条通过（38.31s），-W error；后端全量 Ruff 与 diff check 通过。独立测试库/schema 已清理，命令见 ENVIRONMENT.md。

- 2026-09-16 删除 UI：新增 task-delete.mjs 共 10 个场景分别验收通过；最终布局调整后重复验证删除/菜单 5 组及既有首发/项目切换/历史失败与旧请求隔离 3 组，全部通过。列表 DOM 保持/延迟骨架专项 1 组通过；Workspace 439 条、聊天状态 84 条、TypeScript、全量 ESLint 与 diff check 通过。原删除核心仅修正两处缩进，后续按明确授权调整样式和加载体验。浅/深色 PC 截图已视查，临时服务与独立数据库已清理，命令见 ENVIRONMENT.md。

- 2026-09-16 删除 HTTP/BFF：HTTP 新增 42 条通过，后端 tasks/workspace/local 共 422 条通过（30.91s），-W error 与领域 Ruff 通过；BFF 新增 43 条、Workspace 全量 439 条通过，TypeScript 与 ESLint 通过。真实浏览器删除链路 1 组通过（空任务 204、重复 404、历史任务 409 并保留消息），隔离资源已清理；命令见 ENVIRONMENT.md。

- 2026-09-16 本地会话课：新增 40 条隔离 PostgreSQL 专项通过（4.65s），受影响 local/tasks/chat API/stream/run repository 共 189 条通过（14.21s），-W error；Ruff、修改脚本 ESLint/语法及 diff check 通过。真实工作台 first send 1 组与本地模式 3 组浏览器通过，模型出口模拟，临时服务和测试库自动清理。旧 local-mode.mjs 已补先创建项目、进入草稿、展开运行详情的配套。
- 2026-09-16 空任务删除课：新增隔离 PostgreSQL 专项 27 条（2.14s），Task 领域共 116 条通过（9.14s），-W error；领域 Ruff 与 diff check 通过。独立连接验证提交事实，真实 SQL 错误验证回滚；pg_blocking_pids 验证 Message/Run 插入与删除的锁竞争，覆盖删除提交/回滚及插入先提交。测试库/schema 自动清理，未改开发业务表、迁移或前端。
- 2026-09-16 URL 课：新增 URL 单元测试 13 条，Workspace 全量 396 条、聊天状态 84 条通过；TypeScript、全量 ESLint、夹具 Ruff、diff check 通过。真实 BFF/API/隔离 PostgreSQL 的 PC 浏览器 8 组全部通过，覆盖自动刷新/新标签页恢复、同会话续聊、首发不中断、恢复失败与重试、分页外定位、项目列表失败独立恢复、旧详情/历史竞态。模型出口模拟；1366/1920 截图已视查。临时服务和测试库已清理。Docker Desktop 与项目 PostgreSQL/Redis 已启动并健康，未执行开发库迁移或重置。
- 2026-09-16 详情 BFF 新增 58 条，连同既有读取代理共 73 条通过；Workspace 全量 383 条通过，TypeScript、全量 ESLint 和 diff check 通过。核心无需修正，仅补新路由末尾换行。直接调用实际 GET 路由，上游 fetch 模拟；取消/超时覆盖请求、正文读取与 JSON 完成阶段。本轮未运行浏览器、数据库或模型调用。
- 详情课新增 18 条，与相关 Task 测试共 62 passed（5.41s）；核心无需修正，仅文件末尾换行。
- 目录整理后后端全量 899 passed（58.07s），-W error、Ruff 通过；前端 TypeScript 与 Workspace 325 条测试通过。目录迁移后浏览器主流程定向复跑 1 组通过，隔离资源已清理；命令见 ENVIRONMENT.md。
- 隔离 PostgreSQL 与 psycopg，使用随机独立测试库/私有 schema，允许真实提交，自动清理；禁止 SQLite 或测试开发业务表。
- 浏览器此前 4 组任务交互通过；18px 样式在 1366×768、1920×1080、2560×1318 验证。聊天/标题模型出口模拟，不宣称测试了真实模型生成质量。
- 开发库迁移 head=1b8c75f140de；本课验证 alembic check 无差异。Python 3.12.13 与环境细节见 ENVIRONMENT.md；跨电脑需各自安装依赖与迁移。

本课验收（2026-09-16）：修正模型表名缺少 s、primary_key 参数拼写；新增请求迁移专项 17 条，迁移目录 43 条通过。后端全量 1118 条通过（75.03s，-W error），Ruff 与 diff check 通过。开发库已从 f16a53d928bc 升级至 0a7b64e039cd，alembic check 无结构差异；回退只在隔离测试库演练。请求键在真实空任务删除后保留。交接、课程、计划与重要题库已同步。

## 第 4 周范围核对（2026-09-20）

本次核对当前代码与既有验证记录，不将历史测试通过描述成本轮重新执行。证据命令及原始范围保留在 ENVIRONMENT.md。

| 范围 | 当前结论 | 验收边界 |
|---|---|---|
| 本地 Workspace/目录、Task、PC 工作台 | 已验收 | 目录绑定不授予文件写入或 Shell 权限；源码启动仍需 PostgreSQL/Redis |
| URL/消息/运行列表/时间线/终态摘要恢复 | 已验收，只读恢复 | 刷新后重读持久化事实；未发送草稿、创建重试键与执行检查点不随刷新恢复 |
| Task 创建幂等 | HTTP/BFF/UI 已接通 | 页面内重试复用请求键；响应未确认不自动发送消息，删除后原键拒绝重建 |
| 单会话执行互斥及状态查询 | 两个聊天入口与只读查询均已验收 | 占用保留到必要线程/响应收尾完成；快照和 Run 终态不能证明执行者已停止 |
| 执行并发预算 | 已验收 | 单进程共享实例、两个聊天入口；不覆盖多进程总额与独立工具演示接口 |
| 历史成功/错误摘要 | 已验收 | 唯一且匹配的终态才展示；未知指标不补零；不计辅助标题调用费用 |
| Task 删除 | 空任务及已结束历史任务已验收 | 占用、未结束/未知Run或缺结束时间时拒绝；历史与Task同事务删除，保留目录和创建回执；未知结果不自动重试 |
| 异常停止与执行恢复 | 本机进程退出恢复已验收 | 占用与Run各自记录主机/PID，确认原进程已退出才恢复；无身份/外机/权限失败拒绝；不做checkpoint续跑或外部子进程恢复 |

最近相关证据：后端全量1371条（预算接入课）、前端认证/BFF207条、Workspace703条、聊天状态86条；PC 浏览器会话互斥2组、占用查询5组、预算2组、历史摘要4组。各组模型出口模拟；并非本轮一次性重跑全套。这是范围核对时的基线；最终收尾新增历史删除与进程退出恢复，证据见本页首及ENVIRONMENT.md。

短期状态当前为持久化消息/运行事实加可失效内存缓存，不是执行检查点。第7周原计划继续承接 checkpoint/暂停恢复，第9周承接 Worker/跨进程容量与交付；这些不能反向算作第4周已实现。第4周删除生命周期及异常停止的可诊断边界已补齐；未知来源记录仍不可强制清理。

## 唯一下一课

**第 5 周：attach 异步读取与收尾。**

非TTY原始attach帧增量解析已完成。下一课把解析器接到固定4096字节异步读取：EOF才finish，读取异常/取消不冒充完整输出；立即返回的读取也要给调度机会，解析失败停止本层读取，连接关闭和容器停止由拥有者负责。继续不接真实daemon或HTTP握手，不把CLI已拆分输出交给该解析器。先给完整参考，由学习者实现，明确“完成了”后补内存流/受控读取专项；之后再接固定socket的有界HTTP attach握手及启动前订阅协调。

解析器只接受stdout=1/stderr=2和零保留字节，编号0在本项目stdin关闭策略下拒绝，其他编号同样拒绝；单输入4096字节，单帧正文1MiB为项目上限，不是Docker协议上限。每路捕获65536字节，超限继续消费。只缓存8字节头与剩余长度，不分配整帧；finish只证明位于完整帧边界，不证明连接正常结束、输出完整或命令退出。

退出结果只在一致exited状态解释ExitCode，当前Linux策略接受严格整数0～255，OOMKilled严格布尔、Error严格字符串但只公开是否非空。OOM或daemon错误即使退出码0也不判成功；非零命令退出是已确认失败，不是解析失败。created不算执行完毕，超时/取消原因由编排层记录，不从退出码反推。

启动返回running后责任交给调用方，尚无整体命令超时/输出订阅与运行中取消编排。当前日志driver=none，最终输出需在后续设计启动前订阅/attach协调，不能事后靠logs承诺完整输出。创建后取消/异常并不自动删除；启动尝试后的收尾只核对停止，stop_confirmed=False时必须保留原上下文继续恢复，不能释放资源。快速exited不表示退出码0。

停止服务只处理一致created/running/exited，Paused/Restarting/Dead必须严格False；其他状态保守未确认。已停止目标只读返回，运行目标固定SIGTERM及2秒宽限，再按原完整ID查询确认。客户端超时或取消仍可能留下运行容器，调用方必须保留上下文并后续查询；尚未接执行器的取消收尾。现有清理服务仍仅支持created，不因停止服务新增exited而放宽。

当前策略复核范围限代码列出的字段，不穷举所有Docker安全设置，不证明内核实际限额生效或抵御高权限并发修改。隔离字段的严格文本与类型基于本机Docker真实返回验收；不自动兼容未知序列化格式。执行前仍需生命周期编排和运行/停止证据，未接容器启动。

显式清理只有查询通过created身份、非强制rm回执匹配且随后成功列举确认完整ID缺失才返回完成。查询/删除不是原子事务；不声称抵御宿主高权限并发修改。已有目标缺失、daemon异常或删除响应丢失仍报告清理未确认，不自动重试，不按名称删除。

当前只读核对成功意味着当前响应通过ID（已知时）、名称/标签/批准镜像和created状态检查。未知ID发现不证明目标从未被替换，未完整核对HostConfig或原命令，不是启动授权；不存在和daemon故障仍统一未确认。原token只在调用方内存持有，持久恢复未实现。

客户端当前固定本机macOS Docker Desktop程序与socket，仅回收直接拥有的CLI进程。10秒是等待预算，清理可能更久；CLI退出不证明daemon撤销创建，尚未验证创建响应丢失恢复或容器生命周期。Windows和其他Docker部署未适配。

本课身份快照只确认ID/名称/标签/批准镜像引用及created状态，不完整复核HostConfig/挂载/镜像内容，不是启动授权或永久授权证明。

已验证镜像摘要与重跑命令见ENVIRONMENT.md最后一节。Compose为学习探针，不是通用执行器；创建/启动/超时/取消/daemon失联/进程退出恢复以及按执行身份清理仍须逐步实现。停止证据目前依赖可信Docker daemon报告，并非容器逃逸安全证明；不把本课算作第5周整周完成。

实际命令执行与注册仍须先完成可验证的Sandbox、环境隔离、有界输出、超时/取消和子进程树停止边界；不能仅凭cwd或shell=False声称隔离，也不能提前开放宿主机任意命令。仓库递归搜索/跨文件定位仍未完成，不把单文件搜索当作完整Coding Tools验收。正式进度仍为4/12。

上下文是定位快照，不是持久授权证明；后续文件操作仍复用现有服务重新授权。不使用全局可变上下文，不将ORM Session带入后台线程。

核心仍由学习者亲手编写，明确说“完成了”后才补测试验收。后续仅运行新增及直接受影响测试。读取服务使用逐级dir_fd/O_NOFOLLOW、实际普通文件检查、256KiB+1有界读取、严格UTF-8/NUL策略及描述符清理；支持能力不足的平台拒绝。当前macOS有真实验证，Windows未实机验证。不提供原子内容快照、硬链接来源/挂载隔离或完整Sandbox；同路径目录对象替换/并发搬移也没有完整隔离承诺。

第4周直接实现授权不延续。后续引入外部进程前必须扩展子进程树停止证据与 Sandbox，不能以父进程不存在证明所有子进程已停止；无身份/外机/证据不足仍保守拒绝，无强制清理。

## 尚未解决的问题

- URL 自动选中与消息历史恢复已完成；空任务删除服务、HTTP/BFF/UI 已完成。本地会话已禁止隐式重建；取消终态仍不等于协程停止；有历史任务删除与本机进程退出恢复已完成；持久化checkpoint续跑仍留到第7周。
- 按用户明确要求修复加载闪烁：三栏 WorkbenchShell 移到 keyed TaskChat 外层，右侧详情通过 Portal 跟随当前任务；ProjectGroup 改为稳定项目 key，保留展开状态及已有列表，revision 重置分页并后台刷新；确认删除后排除旧列表目标。会话仍重新读取真实历史，250ms 后才显示静态骨架，快请求不显示加载文字；未引入会话缓存，读取失败仍禁止发送。
- 删除结果未确认时保留查询入口并阻止再次删除；详情 200 也不证明之前的删除已经终止，刷新页面仍会丢失该客户端提示。
- 创建幂等端到端已接通；执行占用已接入普通/流式聊天生命周期，占用只读诊断与进程内预算已完成；有本机退出证据的遗留占用/Run可手动恢复；无身份旧记录仍拒绝。跨进程总限额和持久化执行检查点尚未实现。刷新/关闭会丢失内存中的创建重试键及未发送内容，不能将跨刷新恢复视为已完成。
- API 启动已改为只读迁移版本检查，结构变更须显式 Alembic upgrade。版本一致不保证没有手工结构漂移，不能直接 stamp 跳过缺失结构；此前 Task 空表修复过程保留在 ENVIRONMENT.md。
- 数据库提交与 Redis 通知不属于同一事务，重复取消不补发通知。同会话并发现由占用拒绝重叠，普通聊天收尾会失效缓存；跨系统提交恢复仍待处理。
- 隔离取消曾出现 ASGI callable returned without completing response / Next failed to pipe response；页面停止和授权测试通过，但传输层有序关闭未排查完。
- 当前模型决策使用 stream=False，不可称为逐 Token 生成；文本出现后终态前断网的浏览器验证仍待完成，见 docs/chat-browser-fault-validation.md。
- 辅助标题调用费用尚未计入 AgentRun 指标。真实模型措辞质量没有在模拟浏览器测试中验证。
- 统一安装/依赖准备/迁移启动入口仍需交付阶段完善；源码版目前依赖 PostgreSQL/Redis，不宣称体验者已能零依赖安装。

## 按需文档

- 目录导航：docs/project-structure.md
- 环境、命令与验证证据：ENVIRONMENT.md
- 周大纲：LEARNING_CURRICULUM.md 第 4 周章节
- 教学规则：AGENTS.md；必要时查 LEARNING_COACH_GUIDE.md 对应章节
- 技能：FastAPI/BFF/流式状态先读 skills/agent-streaming/SKILL.md；工具/Agent Loop 先读 skills/agent-runtime/SKILL.md
- 面试题：interview-questions/；优先更新重要已有题目，参考答案已整理不等于已通过模拟面试。
