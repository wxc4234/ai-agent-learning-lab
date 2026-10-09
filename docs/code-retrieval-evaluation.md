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
