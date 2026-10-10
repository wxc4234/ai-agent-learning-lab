# 离线代码检索评测协议

## 作用域与入口

[评测实现](../apps/api/app/services/workspace/files/code_retrieval_evaluation.py)独立于产品运行链路，仅读取显式指定的离线样例并计算来源级指标；不加载产品配置、不访问数据库/模型、不执行样例源码、不自动索引、不添加HTTP或PC入口。没有修改既有扫描、召回、工具或聊天实现。

[固定v1任务集](../apps/api/evaluations/code_retrieval/v1/dataset.json)包含4个Python文件、8个顶层定义和8个问题：6个有答案问题、2个明确无答案问题。取消问题跨control.py/history.py，包含发起取消、检查点和停止后不保存半轮三个相关定义；另有保存/恢复、授权/修订、完整请求字节预算、精确符号、中文跨文件问题，以及无关词和共享词面但无答案的反例。标注由教练设计，`label_origin=coach_authored`，未经过独立双人标注或真实用户验证。

根目录运行，无需启动服务或配置模型：

```bash
.venv/bin/python scripts/evaluate_code_retrieval.py --k 3
.venv/bin/python scripts/evaluate_code_retrieval.py --k 3 --output /tmp/code-retrieval-report.json
```

CLI默认运行词面基线，`--dataset`可指定同协议任务集目录，`--predictions`可消费`Run`结构JSON；只有校验和计算全部成功才输出报告，失败退出码2。缺失或重复问题整次拒绝，不能把未返回的问题静默剔除；真实检索失败须显式提供`status=error`，不得附带排名或引用。输出使用明确路径，调用者应避免覆盖输入；无自动创建目录、重试或部分报告。

## 来源与版本

每个来源有独立ID、相对Python路径、顶层定义名、起止行与整个文件SHA-256。加载时检查文件字节哈希、UTF-8及AST中的唯一顶层定义/行号；AST仅解析，绝不import或执行样例。文件变化即使发生在相关定义外，也需要更新任务集版本证据。路径拒绝绝对路径、上级路径、非规范分隔、盘符与链接；任务集目录由可信离线调用者选择，此读取器不是产品授权路径服务或抗并发路径置换的沙箱。

任务集哈希覆盖schema、全部来源/文件哈希、查询与gold及标注理由；Run必须携带完全相同的`dataset_sha256`。来源ID、问题ID及同文件同定义不能重复，gold不得包含未知或重复来源。JSON最大256KiB、来源文件最大64KiB、最多64来源/64问题、每题最多256原始候选/64引用，重复JSON键拒绝。模型严格拒绝额外字段及错误类型，schema版本必须为整数1。

预测结果按来源ID排序，所有候选均校验，不能仅检查Top K后隐藏坏尾项。未知来源、非法引用行或重复同位置引用整次失败；已知但与问题不相关的来源是合法负例，必须进入指标。引用位置必须在声明定义内，文件哈希通过不代表语义相关。

## 指标口径

- **来源去重**：相同来源保留第一次出现，然后截取Top K；不同来源不得因内容相似而自动合并。报告保留原始重复数量。K是1～64的整数，拒绝bool与浮点数。此口径是来源级排序，不能与未去重的chunk级指标直接比较。
- **Recall@K**：对有答案问题计算`TopK与gold交集数量 / gold数量`，再宏平均。完整gold作为分母，不能把分母裁成K。失败或正常空结果均为0。
- **MRR@K**：有答案问题中第一个相关来源排名的倒数，无命中/失败为0，再宏平均。它只衡量首个相关来源，不能证明跨文件任务所需来源都齐全。
- **无答案问题**：Recall/RR为null，不纳入正例平均；分别记录有返回的假阳性数量、失败数量，以及逐题是否假阳性。失败不算正确拒答，没有正例时整体Recall/MRR也为null。
- **引用相关性**：相关引用数量/全部引用数量；**Top K来源支撑率**：引用来源在本次Top K中的数量/全部引用数量。两者按引用位置微平均，无引用则null，不伪造100%。相关但不在Top K中的引用可被分别识别。合法行号仅证明定位，不证明答案内容被来源支持。

手算验收：q1的gold为A/B，排名C/A/A/B，K=2去重后C/A，Recall=1/2、RR=1/2；q2 gold为C但检索失败，两项均0。因此宏平均均为1/4。无答案q3返回C计一次假阳性，不改变正例分母。专项还覆盖K=1/3、全无答案、无引用和相关性/支撑率不同的结果。

## 透明词面基线与结果

`literal-term-overlap-v1`只提取查询与定义文本中的英文单词/标识符片段，按不同词的交集数量降序排列，零分不返回，并列按来源ID字典序固定。没有停用词、语义向量、中文分词、混合排序或Rerank。排序不读取gold/标注理由；反转来源输入顺序、改写gold后排名不变有专项覆盖。基线把首个结果的定义起始行作为定位引用，`citation_origin=retrieval_reference`，没有生成模型回答；外部回答引用使用`answer_reference`，不可混合冒称同一评测。

[基准报告](../apps/api/evaluations/code_retrieval/v1/baseline-report.json)是v1任务集的确定性回归预期，不是逐课历史。它包含完整逐题候选、命中与失败信息和任务集哈希。当前K=3：Recall=5/6、MRR=5/6；2个无答案中1个假阳性；6个首项定位中5个相关，全部来自Top K。中文问题词面漏检；共享request一词的gzip问题产生假阳性。不能通过删掉这些样例美化结果。

同一小样例既用于开发又用于评测，没有独立测试集；这些分数不估计真实项目泛化、中文语义质量、模型工具选择、回答正确率或提示注入抵抗。没有测量延迟/供应商用量/成本，不填零也不做策略优劣结论；当前Run标签与哈希校验不证明结果来自某个真实供应商。受控向量的真实PostgreSQL链路已按下节接入；真实供应商语义、生产混合检索和独立留出集仍需验证；离线RRF对照见下节。

## 受控向量与词面基线对照

[向量适配](../apps/api/app/services/workspace/files/code_vector_evaluation.py)使用同一个v1任务集，可信隔离夹具复制corpus并建立真实Workspace/Task/Conversation归属；复用生产`generate_and_save_code_batch`执行授权扫描、分块、事务外HTTPX生成和事务内追加保存，随后每题调用`search_code_query`完成当前授权、查询生成、再次授权和真实pgvector余弦排序。测试库/私有schema遵循根conftest，用真实提交和自动清理，不连接开发业务表；本课不变更生产业务函数、HTTP、Agent或PC入口。

受控模型`controlled-token-hash-v1`把英文词经固定SHA-256映射到63个二值槽位，另加0.01非零偏置，共64维。查询与代码使用相同函数，不读取gold、题目ID或标注理由；碰撞、长度归一化与中文无词均会影响排名。这是确定性特征夹具，**不是训练得到的Embedding模型**。HTTP响应不编造Token用量，报告成本/真实供应商用量仍未知。

所有8个完整定义保存在同批。本课以`top_k=20`取回全部8个来源，再交由既有评测器按来源去重并截K=3，避免拿chunk截断后结果与完整词面候选混比。适配核对项目/任务、批次、维度/空间、模型版本、文件哈希、完整覆盖与数量、符号及行号、半开分块坐标、正文/摘要和单块声明；缺失、重复、未知或被拆分的来源拒绝整个评测，不用gold补齐候选。v1只支持单块顶层定义，不声称适用于任意分块策略或大于20来源的任务集。

已知授权/空间/供应商检索失败以`status=error`保留该题，继续维持正例分母及无答案错误数；映射错误和未知异常则中止报告，取消原样传播。当前结果只投影首项定位引用，仍是`retrieval_reference`。pgvector无相关度拒绝阈值，会为无答案问题返回最近来源，不能解释为已找到答案。

[固定比较报告](../apps/api/evaluations/code_retrieval/v1/vector-comparison.json)来自实际隔离PG运行，不包含随机资源ID：

| 策略（K=3） | Recall@3 | MRR@3 | 相关定位/全部定位 | 无答案假阳性 |
|---|---:|---:|---:|---:|
| 词面重合 | 0.8333 | 0.8333 | 5/6 | 1/2 |
| 受控哈希特征 + pgvector | 0.8333 | 0.7500 | 4/8 | 2/2 |

两者任务集哈希与指标口径相同；向量在授权问题的首个相关项位于第二名，中文问题仍漏检。此报告能发现适配/排序变化，不能据此评判真实向量模型优劣或生产检索语义；没有测延迟、真实费用、供应商别名稳定性、混合排序或独立留出集。

根目录运行[独立命令](../scripts/evaluate_code_vectors.py)，数据库测试权限沿用ENVIRONMENT：

```bash
.venv/bin/python scripts/evaluate_code_vectors.py --output /tmp/code-vector-comparison.json
```

命令复用成功场景的隔离pytest夹具，每轮使用独立临时报告，只有子进程成功且数据库/文件夹夹具收尾结束后才复制到指定输出；失败不使用旧报告。固定v1比较文件是回归预期，不在运行时自动改写。手动维护该预期需要核对真实差异，不能用自动刷新掩盖回归。

[17项新增专项](../apps/api/tests/workspace/files/test_code_vector_evaluation.py)通过：真实生成保存及8次查询/余弦SQL、同口径报告、无事务模型等待、文件/批次/向量/绑定不变、9个客户端关闭；供应商/版本/绑定失败分母、来源/版本/范围/正文/分片/重复/覆盖映射拒绝、gold改写不改变实际排名，以及取消关闭与映射失败不伪装成漏检。独立命令实际运行并与固定报告一致；该命令复用其中一个场景，不另算新增测试数量。三份新增Python模块/测试/命令Ruff、Pyright通过。

```bash
cd apps/api
../../.venv/bin/python -m pytest tests/workspace/files/test_code_vector_evaluation.py -q --tb=short -W error
```

本课只新增适配和隔离验收；既有评测公式、生成/召回业务和前端均未修改，不重复全量后端、53项离线公式测试或浏览器验收。核心与配套按当课明确授权由教练完成，不代表独立掌握。

## RRF离线融合对照

[实现](../apps/api/app/services/workspace/files/code_rrf_evaluation.py)融合两份同任务集的完整来源排名，采用`sum(1 / (rank_constant + rank))`。公式依据[Cormack等SIGIR 2009原论文](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf)，本课固定常量60，不用v1标签搜索最佳常量；本项目的缺失候选、失败、去重和并列策略另行明确如下，论文的实验结论不能直接当作本项目效果保证。

每路先按来源ID保留首次出现并重新赋予1起始排名；缺失来源贡献0，不补虚构末位；对完整来源并集求分后排序，最后由共同评测器截取K=3。分数用`Fraction`精确比较，相等按来源ID字典序排序，与输入路次序无关；诊断报告保留两路名次、精确分数和便于显示的小数。融合常量允许1～10000整数，拒绝bool/浮点/越界；它与评测Top K是两个独立参数。融合分数不是相关概率或拒答阈值，不涉及余弦距离与词面得分的直接相加。

输入必须是两个名称不同策略的`retrieval_reference` Run，完整问题集合、任务集哈希、候选和引用位置由现有评测器校验；校验算出的gold指标不会参与排序。重复策略、错误版本、未知候选（包括K以后的尾项）整次拒绝。任一路某题`status=error`，融合该题也失败并保留失败路名；不把另一条路的成功结果当成完整融合。成功空结果允许另一条路贡献候选，两路成功空才得到成功空；两者与失败不能混淆。输出引用重新指向融合首项的定义行，不继承或评价模型答案。

手算：常量1，左路A/A/B去重为A/B，右路B/C/C/A去重为B/C/A。B得1/3+1/2=5/6，A得1/2+1/4=3/4，C得1/3，故B/A/C；对称A/B与B/A同分则固定A/B。专项另验证交换输入及改gold不影响排名、边界常量、缺失/失败/空结果和全集/来源拒绝。

[三策略报告](../apps/api/evaluations/code_retrieval/v1/rrf-comparison.json)复用同次受控向量真实PG结果，融合不增加模型请求。K=3时，词面/向量/RRF的Recall均5/6，MRR分别5/6、3/4、5/6；首项定位相关性分别5/6、4/8、5/8；两个无答案问题的假阳性数量分别1、2、2。RRF调整了授权问题排序，但中文需求依旧漏检，无答案问题仍有候选；不能凭此宣称语义召回或拒答已完成。

根目录运行三策略，原命令不带`--fusion`继续输出原有两策略：

```bash
.venv/bin/python scripts/evaluate_code_vectors.py --fusion --output /tmp/code-rrf-comparison.json
```

命令保留隔离PG/临时目录/受控HTTPX，以及成功收尾后才发布报告的规则。固定报告记录常量、失败策略、逐题两路排名和精确分数，可复现并列与缺失处理；不自动覆盖回归预期。此阶段仍为离线来源级融合，不接生产Agent、自动索引或PC；无真实模型语义、延迟/成本或独立留出集证据。

[26项纯逻辑](../apps/api/tests/workspace/files/test_code_rrf_evaluation.py)和[2项真实PG对照](../apps/api/tests/workspace/files/test_code_rrf_comparison.py)全部通过，后者核对三策略报告、已有两路报告不变、文件/存储不变、9个HTTP客户端关闭，以及供应商失败不降级成功。新增`--fusion`分支影响原命令路径，故仅回归原两策略命令的成功场景；该场景通过，不重复计算为新增测试。4份受影响Python文件Ruff/Pyright通过。既有公式、召回及前端业务未修改，不扩跑53/17项旧专项或浏览器。

```bash
cd apps/api
../../.venv/bin/python -m pytest tests/workspace/files/test_code_rrf_evaluation.py tests/workspace/files/test_code_rrf_comparison.py -q --tb=short -W error
```

## 离线指标核心验收与复跑

[53项专项](../apps/api/tests/workspace/files/test_code_retrieval_evaluation.py)通过：手算指标、去重/排名/失败分母、空gold/无引用、严格类型、问题全集、未知候选、引用位置/重复、版本漂移、AST行号、缺失/超限文件、路径/链接、JSON边界、源码不执行、标签不泄漏到排序、稳定基线以及不同cwd的CLI/外部预测输入/错误退出。三份新增Python实现/测试/脚本Ruff与Pyright通过；无数据库或前端变更，不扩跑既有领域测试、浏览器或账号专项。

```bash
cd apps/api
../../.venv/bin/python -m pytest tests/workspace/files/test_code_retrieval_evaluation.py -q --tb=short -W error
```

核心、数据与测试由教练按本课明确授权完成，工程验收不等于学习者独立掌握。

## 无答案判定与开发留出评测

[开发集](../apps/api/evaluations/code_retrieval/abstention-v1/development.json)包含4个正例、2个无答案问题；先运行开发阶段并保存[冻结策略](../apps/api/evaluations/code_retrieval/abstention-v1/policy.json)，再编写并运行[留出集](../apps/api/evaluations/code_retrieval/abstention-v1/holdout.json)的4个正例和4个无答案问题。两组共享经过字节摘要/AST校验的v1语料，问题ID与规范化文本不得重合。它们由教练编写，属于问题级留出，不能称为独立人工盲测或未见项目泛化。

候选来自完整词面/受控向量RRF排名。规则只计算查询与候选定义共享的不同英文词数量（正则与词面基线相同），达到阈值才保留，保持原排序并重建首项引用。它不读取gold，不使用RRF分数作为概率。预先固定阈值网格1/2/3/4、K=3和目标：最小化“1减正例宏平均Recall + 无答案假阳性率”的一半；并列时先取较高Recall，再取较低阈值。用精确分数比较，避免浮点并列误差。开发集包含两类且全部检索成功才能校准。策略绑定规则、来源版本、开发集摘要与目标；留出阶段只加载策略，不再校准。

[开发报告](../apps/api/evaluations/code_retrieval/abstention-v1/development-report.json)选出阈值2：Recall@3=1，误拒答0/4，无答案假阳性从2/2降为0/2。[留出报告](../apps/api/evaluations/code_retrieval/abstention-v1/holdout-report.json)保持阈值2：Recall@3=0.75，误拒答1/4，无答案假阳性2/4。中文正例被拒绝；调度优先级和密码重置虽然不存在，仍因共享标识符被保留。因此本规则只作为离线实验，不能接入生产并宣称能可靠拒答。观察留出结果后未修改阈值；以后据此优化时，该留出集应降为已观察回归集，另建新留出版本。

报告明确保留四种状态：retrieval_error、no_candidates、abstained、retained。错误仍进入正例召回分母，但不计作正确拒答；原本无候选与过滤后主动拒答分别统计。误拒答率分母是全部正例，无答案假阳性率分母是全部无答案题；缺少相应类别时为null。候选保留不证明回答正确，来源定位也不等于模型回答引用质量。纯过滤模块信任加载器提供的已验证片段，不替代生产授权。

根目录复跑，两步必须使用同一个新策略路径（已存在时校准拒绝覆盖）：

```bash
.venv/bin/python scripts/evaluate_code_abstention.py calibrate --policy /tmp/abstention-policy.json --output /tmp/abstention-development.json
.venv/bin/python scripts/evaluate_code_abstention.py evaluate --policy /tmp/abstention-policy.json --output /tmp/abstention-holdout.json
```

两阶段均实际运行通过；复用隔离PostgreSQL与受控HTTPX，模型请求在事务外，测试资源清理成功后才发布外部报告。策略和报告不是跨文件原子事务，写报告失败时已生成的策略仍保留。版本化预期报告用于确定性回归，不自动覆盖；只证明受控召回/统计/冻结流程，不证明真实模型语义、成本或延迟。

[28项纯逻辑/命令边界](../apps/api/tests/workspace/files/test_code_abstention_evaluation.py)和[2项真实PG比较](../apps/api/tests/workspace/files/test_code_abstention_comparison.py)通过，覆盖网格/并列、去重/引用重建、四状态、标签独立、类型/版本/角色/集合重合拒绝、冻结路径不覆盖，以及留出禁止校准、策略/存储/语料不变和客户端关闭。4份新增Python文件Ruff/Pyright通过。生产检索及既有评测模块未改动，未扩跑旧专项或浏览器。

```bash
cd apps/api
../../.venv/bin/python -m pytest tests/workspace/files/test_code_abstention_evaluation.py tests/workspace/files/test_code_abstention_comparison.py -q --tb=short -W error
```

## 延迟与模型用量观测

[观测器](../apps/api/app/services/workspace/files/code_evaluation_observation.py)由[离线PG入口](../apps/api/tests/workspace/files/test_code_evaluation_observation_pg.py)包装现有函数，生产调用与旧固定质量报告保持不变。单调时钟记录每题literal、embedding、recall、fusion、filter、end_to_end；建库独立记录embedding与end_to_end。查询端到端从词面检索前开始，到过滤完成为止，包含授权、映射、调度和校验开销；不包括题集加载、初始建库与最终指标计算。recall含重新授权、连接/事务及映射，并非纯SQL服务器执行时间。子阶段嵌套于端到端，不能把各阶段P95相加。

汇总按phase/stage/status分组，报告样本数、均值、nearest-rank P50/P95；成功、错误和取消不混为成功延迟。失败原样抛出并保留已耗时；本入口在单题已知检索错误后继续下一题，取消立即停止，planned/started/completed/unstarted保留未执行数量。没有样本时没有伪造的零分位值。时钟倒退或非有限值使观测失败。同步代码取消仍受既有执行边界限制。

用量通过独立transport包装计数实际尝试，并在生成器完成验证后收集prompt/total；模型成功后召回失败仍保留已报告用量。传输尝试不证明供应商收到请求或产生账单。每题记录reported、unknown或not_requested；任一有请求样本缺少完整用量，阶段总Token保持null，逐题已知用量仍可查。建库只计一次，不把存储的历史用量加进每次查询。没有价格和账单依据，cost始终null；显式报告0与未报告不同。生成过程中途失败的部分用量目前保守记unknown，不从文本长度或已完成响应推算完整账单。

根目录运行：

```bash
.venv/bin/python scripts/evaluate_code_observation.py --output /tmp/code-retrieval-observation.json
```

命令复用自动清理的隔离PostgreSQL、临时语料和受控HTTPX，成功收尾后才发布报告；失败不覆盖既有输出。[本机测量样本](../apps/api/evaluations/code_retrieval/v1/observation-sample.json)是2026-10-09的一次串行运行，不作为精确耗时回归预期：建库1次48.277ms；8题查询端到端P50=8.175ms、P95=12.934ms。未额外预热，8个不同问题各执行一次，P95在此样本量等于最大值。建库1次/查询8次请求，用量均unknown。这不是压测或真实供应商性能/费用；受控报告用量10/12仅用于测试聚合分支，不进入本机样本账单。

[17项纯逻辑与命令边界](../apps/api/tests/workspace/files/test_code_evaluation_observation.py)和[5项真实PG场景](../apps/api/tests/workspace/files/test_code_evaluation_observation_pg.py)通过：可控时钟/嵌套/分位、异常/取消、空样本、非法/重复/不完整用量、已知0/未知/未发送、transport关闭、发布失败，以及unknown/reported/provider_error/recall_error/cancelled整链路。独立命令实际成功，4份新增Python文件Ruff/Pyright通过。生产与既有评测模块未修改，不扩跑旧专项或浏览器。

```bash
cd apps/api
../../.venv/bin/python -m pytest tests/workspace/files/test_code_evaluation_observation.py tests/workspace/files/test_code_evaluation_observation_pg.py -q --tb=short -W error
```

## 真实供应商适配与配置预检

[通用映射](../apps/api/app/services/workspace/files/code_evaluation_mapping.py)显式接收Embedding配置与预期报告模型，校验请求模型、报告模型、维度、空间、全部来源版本和片段，不再固定64维。原受控适配仅负责提供固定模型/配置，原有报告口径不变。[评测执行器](../apps/api/app/services/workspace/files/code_provider_runner.py)复用授权生成保存和授权单查询，在隔离学习项目内比较词面/向量/RRF，并复用用量和单调耗时观测；不改变产品Agent、自动索引或真实供应商的拒答策略。英文阈值实验不自动迁移到新模型。

[预检与发送预算](../apps/api/app/services/workspace/files/code_provider_evaluation.py)只加载仓库固定v1学习语料，没有任意项目路径参数。先校验文件摘要/AST、独立Embedding配置、预期报告模型、预计请求数与HTTPX实际JSON序列化字节。默认9次、单请求32KiB、总128KiB，可显式收紧；上限分别32次、256KiB、1MiB。当前语料8段定义一批、8题分别查询，默认预计9次/2621字节。它是请求次数/字节边界，不是Token或费用预算。

发送前复核POST目标与完整模型参数、固定文本白名单计数和实际字节预算；新增正文、重复发送或预算超限均在传输前拒绝。失败尝试不退款、不重试；HTTP不跟随重定向、不使用环境代理。真实模式必须显式开启；配置指纹（密钥使用Pydantic掩码，不校验密钥轮换）和语料清单在CLI父子进程间复核，地址/空间配置漂移不能静默继续。密钥不进入命令行、报告或错误正文。真实网络实际目的地仍由用户配置决定，不声称具备DNS固定或通用SSRF防护。

根目录使用：

```bash
# 默认仅预检、零请求，不需要真实密钥。
.venv/bin/python scripts/evaluate_code_provider.py

# 默认受控传输，真实隔离PostgreSQL；清理成功后发布报告。
.venv/bin/python scripts/evaluate_code_provider.py --run --output /tmp/code-provider-evaluation.json

# 独立Embedding配置沿用ENVIRONMENT；预期报告模型必须由用户明确提供。
.venv/bin/python scripts/evaluate_code_provider.py --mode real --response-model YOUR_REPORTED_MODEL

# 显式发送固定学习语料；可能产生供应商费用。
.venv/bin/python scripts/evaluate_code_provider.py --mode real --response-model YOUR_REPORTED_MODEL --run --allow-network --output /tmp/code-provider-real.json
```

真实模式不从响应自动接受新版本。建库报告模型错配停止后续查询，已发生的请求不能撤销；建库暂存批次随隔离库清理。查询供应商/空间错误保留为失败题，不改变分母；取消传播并停止整次执行，不发布不完整比较报告。映射数据不兼容整次失败。命令异常/清理失败保留旧输出，不打印供应商异常详情。

观测报告记录建库/查询端到端、数据集级词面/融合耗时及用量。此通用执行器没有进一步拆开查询内部HTTP与SQL耗时；不能与前课单题过滤端到端直接等同比较。查询在生成后、组合结果返回前失败时，用量保守为unknown（既有细粒度包装测试能捕获该用量，但尚未接入通用执行器）；建库中途失败也不推算费用。取消/建库失败不发布质量报告，内部资源仍由既有finally/fixture清理。真实模式报告环境标明configured real provider，受控模式明确标记controlled。

验收：新增22项预检/命令边界及8项真实PG受控执行通过；专用命令用例在普通pytest中跳过，已通过上述受控命令单独实际执行。总计31个新增用例有执行证据。覆盖替代67维/请求别名/报告版本、错版本/错维度、供应商失败、取消、未授权零发送、无事务跨HTTP、语料不变与客户端关闭；预算/重复/正文/目标/参数拒绝、缺配置脱敏、网络显式开启、配置指纹及失败不覆盖报告。来源映射抽出影响原适配，原17项适配专项通过；默认新命令词面/向量质量报告与原固定基线一致（仅策略名不同）。7份受影响Python文件Ruff/Pyright通过。

```bash
cd apps/api
../../.venv/bin/python -m pytest tests/workspace/files/test_code_provider_evaluation.py tests/workspace/files/test_code_provider_runner.py -q --tb=short -W error
```

本课未启用真实请求，未读取/发送用户项目；未取得真实供应商语义、延迟或费用结果。上述命令支持现有兼容Embedding协议，不宣称支持任意供应商私有协议。核心/测试由教练按当课授权实现，不代表学习者独立掌握。


## 百炼真实固定语料实测

2026-10-10，按用户授权接入北京地域百炼 `text-embedding-v4`；一次固定文本探测确认响应模型同名、默认1024维，不请求自定义维度。随后复用上述真实CLI与隔离PostgreSQL，建库1次、查询8次，共9次请求/2558字节；专用命令通过，清理完成后发布[原始比较报告](../apps/api/evaluations/code_retrieval/v1/provider-real-comparison.json)。配置和密钥仅在本机忽略文件中，报告不包含密钥、工作空间域名或用户项目正文。探测另计1次/5 Token，不混入评测9次请求。

| 策略 | 正例Recall@3 | 正例MRR@3 | 无答案假阳性 | 错误题 |
| --- | --- | --- | --- | --- |
| 关键词 | 0.8333 | 0.8333 | 1/2 | 0/8 |
| 真实向量 | 1.0000 | 1.0000 | 2/2 | 0/8 |
| RRF融合 | 1.0000 | 1.0000 | 2/2 | 0/8 |

分母为6个正例、2个无答案问题，来源先去重再截3；引用仍是检索定位，不是聊天回答质量。逐题排名在报告rows中，不修改原标签或用本次结果反调参数：

- 关键词唯一漏检的正例是“停止运行以后不能保存半轮对话”，Recall为0；真实向量返回 `history.save_turn`、`history.restore_history`、`control.cancellation_checkpoint`，两个相关定义全部命中，Recall为1，但其中恢复历史仍是不相关候选。
- 其余五个正例三种策略均找全标注来源。RRF改变部分顺序，在本次小样本没有比向量进一步提高汇总召回/MRR，不能据此宣称融合一定更好。
- `absent`（天文查询）在关键词中为空，向量与融合仍返回历史/取消代码；`absent-related`（gzip解压）三路均返回无关代码。距离最近不代表语料中存在答案，RRF只是融合排序，没有拒答能力。
- 检索首项引用相关率：关键词5/6，向量及融合6/8；后两者给无答案题也生成定位引用，导致该指标下降。正确的文件行号不能证明相关性。

建库端到端371.3ms；8题查询端到端均值391.8ms、P50 329.9ms、P95 933.9ms。词面全题本机计算约0.90ms，融合全题计算约1.42ms；后者不包含模型和数据库，不与向量端到端直接比较，也不加分位数。供应商报告建库315 Token、查询47 Token，共362 Token；加上单独探测共367 Token。实际账单/免费额度未读取，费用仍unknown。8题单次P95就是最大值，不能作为生产延迟承诺。

本次只证明固定8段代码上的真实服务连通、模型空间校验、授权生成/查询/PG清理及该题集召回结果；不是独立泛化验证，也没有真实聊天回答质量评测。未启用生产混合检索、拒答规则或自动索引。报告中的各策略 `limits` 属于通用评分器本身的限制，真实HTTP耗时/用量另见顶层 `observation`，不把评分器直接当作模型语义测量器。


## 真实向量距离观测

[距离观测器](../apps/api/app/services/workspace/files/code_distance_observation.py)接在通用执行器的完整来源/空间映射之后，保留相同候选顺序的来源ID、1起始排名、余弦距离、是否相关与是否Top 3。不回传正文或向量，不依据标注修改排序/模型请求。余弦距离越小越近，但不是置信度；非法数值、乱序、排名/长度/查询不匹配会拒绝发布。错误题与成功空结果分别计数，最近距离/前两名间隔为null，不能填0冒充高相似度。错误/取消的原处理和事务边界不变，观测仅在HTTP与事务退出后消费内存结果。

2026-10-10以同一百炼`text-embedding-v4`默认1024维重跑固定8段/8题，原因是前课仅保存排名、隔离向量已清理，不能事后还原距离。[本轮原始报告](../apps/api/evaluations/code_retrieval/v1/provider-distance-observation.json)独立保留，未覆盖前课报告。本轮9次请求/2558字节、全部成功，供应商报告315建库+47查询=362 Token，无额外维度探测。实际费用unknown；隔离PG清理成功后发布，未发送用户项目。

| 查询 | 有答案 | 最近余弦距离 | 前两名距离间隔 |
| --- | --- | --- | --- |
| cancel | 是 | 0.3369 | 0.0207 |
| history | 是 | 0.2426 | 0.0664 |
| authorization | 是 | 0.4355 | 0.0545 |
| budget | 是 | 0.4764 | 0.1062 |
| exact-symbol | 是 | 0.3647 | 0.1269 |
| chinese | 是 | 0.3885 | 0.0513 |
| absent | 否 | 0.7846 | 0.0057 |
| absent-related | 否 | 0.7098 | 0.0093 |

全部64候选中12个相关、52个不相关。相关候选距离范围0.2426～0.5884，不相关候选0.4398～0.9033，两者重叠；这些是同一查询下的相关样本，不是64个独立问题。中文题第二名`history.restore_history`不相关但距离0.4398，第三名`control.cancellation_checkpoint`相关但距离0.5884；单一距离过滤不能在本样本同时排除前者并保留后者。最近距离能描述整题是否可能有答案，不能保证每个返回片段都相关；小间隔也可能只是多个有效来源并列，不能当错误概率。

本次6正例最近距离范围0.2426～0.4764，2无答案题0.7098～0.7846，观察上分开；没有据此选择、试验或发布阈值，不能从已观察数据得出独立泛化结论。后续应先固定开发/留出角色、目标和搜索空间，再冻结规则。三策略排名质量与前课一致，向量与RRF仍有2/2无答案假阳性；新增观测没有改变产品检索或启用拒答。

验证：18项纯内存测试覆盖标签不改排序、分组/间隔、错误与空结果、并列/单候选/边界、NaN/无穷/布尔/字符串/越界/乱序、查询/数量/重复/排名错配；直接受影响的8项执行器PG受控场景通过，另有真实CLI专用场景1项通过。4份相关Python文件Ruff/Pyright通过。无需重跑未改动的前端、聊天或全量后端。

```bash
# 根目录；PG场景沿用ENVIRONMENT的隔离夹具，普通测试不联网。
.venv/bin/python -m pytest apps/api/tests/workspace/files/test_code_distance_observation.py apps/api/tests/workspace/files/test_code_provider_runner.py -k 'not test_provider_command' -q
```

## 真实距离开发集校准与冻结

[固定开发集](../apps/api/evaluations/code_retrieval/distance-v1/development.json)沿用已验证的8段源码，另写4正例/4无答案问题，ID与规范化文本不得与已观察v1重合；它在观察v1后编写，不能冒称独立留出。未建立或读取本轮留出数据。`--dataset distance-development`只切换到这一固定文件，不能指定任意项目路径；白名单、次数/字节限制和CLI父子清单核对均绑定实际开发题。原命令默认`baseline`保持原语料。

真实采样前保存[设计记录](../apps/api/evaluations/code_retrieval/distance-v1/design.json)：查询级最近余弦距离≤阈值则保留原候选，否则整题拒答；不逐片段过滤、不改变排序。阈值网格固定0～2、步长0.1，等权平均误拒答率FNR和误接受率FPR。并列依次按损失、FPR、较小阈值裁决，使用分数精确比较；等权与更保守的并列规则是教学实验选择，不等于产品真实代价。配置/数据清单及校准器源码摘要在采样前固定，文件时间和哈希不是外部防篡改证明。

2026-10-10运行北京百炼`text-embedding-v4`默认1024维，9次请求/2831字节全部成功；建库315、查询106，共421个已报告Token，无额外探测。隔离PG清理后发布[开发原始报告](../apps/api/evaluations/code_retrieval/distance-v1/development-report.json)。未调用聊天模型、未发送用户项目、未核对实际费用账单。

[校准器](../apps/api/app/services/workspace/files/code_distance_calibration.py)仅离线读取真实开发报告，验证语料摘要、问题/来源全集、排序/数值与标签一致性；失败或空候选阻止冻结，不能删题或当作正确拒答。运行决策函数只读取距离、状态与阈值，gold仅供开发评分。冻结绑定语料、开发题、完整报告、配置指纹、请求/报告模型和维度；别名不能证明供应商权重永不变化，当前配置指纹对密钥使用掩码。冻结果标记`development_only_not_validated_on_holdout`，命令独占创建输出，拒绝覆盖已有文件。

| 阈值 | 误拒答/4正例 | 误接受/4无答案 | 平衡损失 |
| --- | --- | --- | --- |
| 0.0～0.4 | 4 | 0 | 1/2 |
| **0.5（冻结）** | **1** | **0** | **1/8** |
| 0.6 | 0 | 2 | 1/4 |
| 0.7～2.0 | 0 | 4 | 1/2 |

完整21项试验和逐题决策见[冻结结果](../apps/api/evaluations/code_retrieval/distance-v1/frozen-policy.json)。0.5保留3个正例、拒答4个无答案，误拒答`dev-bytes`：最近距离0.5506。阈值0.6虽然保留该题，却误接受OAuth刷新和SSE分块两个未实现功能；不根据看到的间隙再加0.55等网格点。保留后的正例来源Recall@3为2/3，查询级判定与来源召回口径不同，不能把3/4题被保留当成来源召回率。开发集误接受为0不代表产品安全或留出表现。

复跑（先生成新报告，再离线冻结到新文件；不要覆盖本次冻结结果）：

```bash
.venv/bin/python scripts/evaluate_code_provider.py --mode real --response-model text-embedding-v4 --dataset distance-development
.venv/bin/python scripts/evaluate_code_provider.py --mode real --response-model text-embedding-v4 --dataset distance-development --run --allow-network --output /tmp/distance-development-new.json
.venv/bin/python scripts/calibrate_code_distance.py --report /tmp/distance-development-new.json --output /tmp/distance-policy-new.json
```

验证范围：27项新增纯内存/CLI测试覆盖目标与并列、边界/四种状态、非开发/非真实/缺类/失败/空结果/伪造排名/标签/非有限值/来源错配拒绝、开发白名单、观测题重合和冻结文件不覆盖；数据选择改变发送清单，因此回归原22项预算/CLI边界；执行器读取开发题，因此9项隔离PG受控场景覆盖新增开发路径及既有成功/失败/取消路径。合计58项定向测试通过，另有真实CLI专用场景通过。7份受影响Python文件Ruff/Pyright通过。无前端、生产检索、工具或Agent Loop修改，不扩大其回归范围。

## 冻结距离规则的问题级留出评估

[留出题](../apps/api/evaluations/code_retrieval/distance-v1/holdout.json)为冻结后编写的4正例/4无答案新问题，沿用相同8段语料；检查ID及NFKC/大小写/空白规范化后的文本与v1、开发题不重合。它是问题级留出，不是新代码仓库；教练已经看过开发结论，也不是双盲或真实用户分布样本。语义近似和编题偏差无法由字符串去重消除。

真实采样前记录[留出设计](../apps/api/evaluations/code_retrieval/distance-v1/holdout-design.json)，绑定整个冻结文件摘要、留出题摘要与0.5阈值。`--dataset distance-holdout`只选择该固定文件；发送前检查冻结来源、开发报告、语料及模型配置/响应模型/维度，任何变化拒绝预检。基线和开发路径不会读取留出文件。HTTP仍限固定白名单/9次/正文预算，生成/查询授权与事务边界不变。

2026-10-10在百炼`text-embedding-v4`默认1024维完成9次请求/2954字节；建库315、查询134，共449个供应商已报告Token，无额外探测。隔离PG清理后保存[原始报告](../apps/api/evaluations/code_retrieval/distance-v1/holdout-report.json)，然后[离线评估器](../apps/api/app/services/workspace/files/code_distance_holdout.py)只应用冻结规则，不搜索阈值、不写回冻结文件。[留出结论](../apps/api/evaluations/code_retrieval/distance-v1/holdout-evaluation.json)独占创建，拒绝覆盖旧结果；真实费用未核对。

| 指标 | 开发集 | 本次问题级留出 |
| --- | --- | --- |
| 阈值（距离≤阈值保留） | 0.5 | 0.5，未改动 |
| 误拒答 | 1/4 | 0/4 |
| 无答案误接受 | 0/4 | 2/4 |
| 正确主动拒答 | 4/4 | 2/4 |
| 拒答后正例来源Recall@3 | 2/3 | 1 |
| 检索错误/空结果 | 0/0 | 0/0 |

本次留出原始向量正例Recall@3亦为1；规则保留全部4正例，没有降低其来源召回。无答案错误具体为：

- `hold-resume`：要求从持久检查点自动恢复已取消任务，最近距离0.3537；语料只有取消与停止检查，没有恢复实现，仍被保留。
- `hold-truncate`：要求请求超过字节预算时自动截断，最近距离0.3734；语料只测量与拒绝超限，没有截断实现，仍被保留。
- 删除会话并清理数据库、校验Webhook HMAC两个无答案题距离分别0.6050/0.6277，被正确拒答。

四正例最近距离0.3315～0.4348，与前两个无答案题重叠；语义接近不能证明功能已实现。**工程评估完成，不代表规则达到生产可用标准**。本课不依据留出结果修改阈值、不重新校准、不启用产品拒答。未来若利用这些错误改规则，本组问题就成为已观察数据，需要新的验证边界；可继续研究关键词/语义互补与证据充分性，但不能声称本次简单距离规则已解决无答案问题。

输出分开保留`retrieval_error`、`no_candidates`、`abstained`、`retained`。错误题仍留在原正例Recall分母中记0，不作为正确拒答；误拒答率仅统计主动误拒答，需和正例失败/无候选计数一起阅读。查询级保留不筛选单个候选；定位引用不等于最终回答事实正确。原冻结文件保留其开发阶段状态，留出结论在独立评估文件中，不回写旧实验。

```bash
# 根目录，先预检；实际发送仅在明确继续本实验时执行。
.venv/bin/python scripts/evaluate_code_provider.py --mode real --response-model text-embedding-v4 --dataset distance-holdout
.venv/bin/python scripts/evaluate_code_provider.py --mode real --response-model text-embedding-v4 --dataset distance-holdout --run --allow-network --output /tmp/distance-holdout-new.json
.venv/bin/python scripts/evaluate_distance_holdout.py --report /tmp/distance-holdout-new.json --output /tmp/distance-holdout-result-new.json
```

验证：26项新增纯内存/CLI用例覆盖冻结来源/报告/数据角色/重合拒绝、模型空间错配预检、排序/非法距离/标签/查询与来源错配、四种决策的独立计数和既有文件保留。数据选择影响发送范围，回归原22项发送预算/CLI边界；数据库仅选择直接受影响的基线、开发和新增留出3条受控路径。合计51项定向测试通过，真实CLI专用场景另通过；6份相关Python文件Ruff/Pyright通过。无前端或生产策略变化，不运行其回归或全量后端。
