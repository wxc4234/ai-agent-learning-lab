# 受控代码向量存储协议

当前仅有内部单批保存与授权批次读取，没有HTTP、Agent工具、PC入口、自动扫描/发送链路或相似度召回。实现见[存储服务](../apps/api/app/services/workspace/files/code_vector_storage.py)与[仓储](../apps/api/app/repositories/workspace/code_embedding_repository.py)。输入使用[Embedding生成协议](code-embeddings.md)的受控内存结果。

## 调用与授权边界

1. 可信宿主显式选择当前身份、Workspace/Task及允许发送的正文范围。在授权读取与模型请求之前调用 `capture_code_embedding_target`，捕获目录路径和绑定修订；函数只核对数据库归属，不验证磁盘目录是否存在。
2. 捕获事务已结束后，独立完成本轮授权分块和显式Embedding生成。它们不会自动调用存储，也不能把旧结果当作当前授权。
3. `save_code_embedding_batch(target=..., source=..., config=...)` 先在事务外验证全部内存结果，再开启短事务，按项目→任务→会话顺序锁定并重新核对归属、任务范围和绑定。目标路径或修订发生变化时拒绝；改到另一目录再改回来也不能复用旧目标。
4. `load_code_embedding_batch` 必须提供当前身份/项目/任务、批次ID和明确模型空间配置/报告版本。同样持有归属锁，并按当前绑定、指定任务和完整空间指纹过滤；未知、越权、错空间或旧绑定统一拒绝。

目标DTO、批次ID、分块ID和空间ID都不是许可。当前接口仅供可信宿主使用，不接受模型自行选择目标；任意内存DTO不证明真实文件读取、发送许可或当前文件版本。调用方必须遵守上述顺序；服务校验的是声明的来源一致性，不重新读取磁盘。文件外部修改不会增加数据库绑定修订，摘要只描述生成时的版本。

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

## 迁移与环境

[迁移](../apps/api/migrations/versions/6f4c2b8d901a_add_code_embedding_storage.py)显式安装/复用public中的vector扩展并创建三表。需要扩展已在服务器安装以及创建扩展的数据库权限；已在其他schema安装时拒绝，不偷偷移动它。当前Compose镜像提供该扩展，依赖安装和向前升级按[环境说明](../ENVIRONMENT.md#3-数据库服务与迁移)执行；API启动不自动DDL。

降级先锁三表，任一表非空即拒绝丢失快照/空间元数据；空表可降级再升级。降级保留共享vector扩展，不操作其他schema。数据库测试仍仅用根夹具的随机数据库/私有schema，public只提供共享vector类型和函数，应用表仍在私有schema。

## 验证入口

在 `apps/api` 执行：

```bash
../../.venv/bin/python -m pytest -q tests/workspace/files/test_code_vector_storage_validation.py tests/workspace/files/test_code_vector_storage.py tests/migrations/test_code_embedding_migration.py
```

[71项预检测试](../apps/api/tests/workspace/files/test_code_vector_storage_validation.py)验证无效批次不开事务、静态拒绝、跨平台路径、定义分片完整性及元数据/数值矩阵；[42项真实PostgreSQL测试](../apps/api/tests/workspace/files/test_code_vector_storage.py)验证类型和float32、1/2/4096维、精确正文/数量预算、模型空间、当前归属与绑定、并发跨项目、真实提交及晚期/提交/子插入失败回滚、任务级联与回滚；[3项迁移测试](../apps/api/tests/migrations/test_code_embedding_migration.py)验证真实升级/元数据一致、空表降级再升、非空拒绝和DDL失败回滚。

定向回归只包含生成投影/空间/空输入3项、任务删除成功/回滚5项、启动版本只读检查4项。共116项新增与12项回归通过；不是全量后端或账号模式专项。真实供应商、语义效果、自动项目索引、召回/上下文、浏览器和Windows未在本课验收。
