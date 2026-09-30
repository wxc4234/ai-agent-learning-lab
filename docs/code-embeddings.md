# 代码 Embedding 配置与批量生成协议

当前仅有内部配置和生成服务，没有HTTP/Agent工具/PC入口或向量召回。输入是可信调用方显式选择的[内存代码分块](code-inventory.md#python-代码分块)；生成服务不读项目文件、不查询或提交数据库，不凭分块ID授予当前访问权。独立[受控向量批次存储](code-vector-storage.md)已提供，但尚未自动装配扫描、发送或入库链路；装配时仍须重新确认归属、目录绑定与发送范围，不能直接重放旧快照。

## 配置与调用

连接配置由[应用Settings](../apps/api/app/config.py)读取根目录 `.env`，调用时由[独立配置契约](../apps/api/app/services/model/embedding_config.py)校验；未完整配置时原有聊天仍可启动，生成调用返回 `embedding_not_configured`。不复用聊天密钥、地址或模型，不在模块导入/应用启动时创建Embedding客户端或发送请求。

| 环境变量 | 契约 |
|---|---|
| `EMBEDDING_API_KEY` | 独立密钥，默认空；1～4096个可用于Bearer认证头的可见ASCII字符，以SecretStr保存 |
| `EMBEDDING_BASE_URL` | API根地址，不含 `/embeddings`；远程HTTPS或回环HTTP；不含URI凭证、查询或片段，最多2048个ASCII字符 |
| `EMBEDDING_MODEL` | 用户选择的Embedding模型名称，默认空；不自动选择聊天模型，最多256个Unicode码点/1024 UTF-8字节 |
| `EMBEDDING_DIMENSIONS` | 显式预期维度，1～4096的严格整数；空值表示尚未配置，环境文本不接受浮点、前导零或空格 |
| `EMBEDDING_REQUEST_DIMENSIONS` | 默认 `false`；仅供应商支持时设为 `true`，才在请求中发送dimensions字段；始终校验实际返回维度 |
| `EMBEDDING_TIMEOUT_SECONDS` | 默认30，1～60秒，最多三位小数；HTTP批次总墙钟与单次I/O超时，不是解析进程的硬资源隔离 |

用户填写 [.env.example](../.env.example) 对应字段；实际 `.env` 不提交。普通兼容供应商是否支持指定模型、维度参数及API地址须由用户实际配置和单独验证，本课没有确认任何供应商的模型可用性。

生成接口为 `generate_code_embeddings(source: PythonCodeChunks, *, config: EmbeddingConfig | None = None, transport: httpx.AsyncBaseTransport | None = None)`，返回 `CodeEmbeddings`；未传config时延迟加载当前Settings。transport仅供可信测试注入，不对外暴露。调用方必须明确选择是否发送项目正文；当前验收只发送人工夹具给MockTransport，无外部网络或真实项目发送。

HTTPX使用 `POST {base_url}/embeddings`，正文仅有 `model`、文本列表 `input`、`encoding_format="float"` 及可选 `dimensions`；Key仅在认证头中解封，不发送相对路径、分块ID、任务或用户身份。协议字段依据[OpenAI官方Embeddings API](https://developers.openai.com/api/reference/resources/embeddings)。不同供应商的实际兼容性尚未验收。

## 预算与失败边界

先验证全部输入，再发送第一批。每次最多20片，每片最多2000个Unicode码点/4096 UTF-8字节，总正文最多80 KiB；空串拒绝，非空的空白/换行片段保留，不能删项后错配索引。分块ID需为唯一的64位小写hex，正文摘要与文件来源摘要必须一致；这些完整性检查不构成授权证明。字符/字节限制不是模型Token预算，超出供应商模型限制时仍可能被拒绝，本课不估算Token或冒称通用Tokenizer。

按原输入顺序分批，每批最多8片/16 KiB正文，顺序请求，最多5批。字节预算指输入正文，不是完整JSON；JSON转义另有有限开销。没有自动重试、重定向或继承环境代理；一个总墙钟超时覆盖全部请求及HTTP上下文收尾。取消继续传播，关闭当前响应和客户端，不伪装成业务成功。

仅成功HTTP响应进入正文读取；失败/重定向响应不读取供应商错误体。响应需为JSON媒体类型且无压缩，实际读取最多1 MiB，分段累加后严格UTF-8/JSON解析；拒绝重复键、非有限JSON常量、解析/递归失败，不返回原始错误正文或凭证。响应上限约束读取/结果规模，没有独立解析进程或底层网络内存的硬上限保证。

每批 `data` 数量必须等于输入数；每项的 `index` 必须是严格整数，恰好覆盖0至n-1，无重复、缺失或越界。按index重新排序后关联原分块，不按供应商列表顺序zip。向量必须是规定维度的列表，每项仅接受int/float、拒绝bool/字符串/空值，转换后必须有限；不静默裁维、补零或归一化。数值有限不证明向量有语义质量、能转为任意数据库类型或适用于所有距离度量。

返回模型名称可为请求别名对应的版本，但同次所有批次必须报告一致版本。缺失usage保留未知；已有usage必须有非负严格整数 `prompt_tokens` / `total_tokens`，后者不能小于前者。任一批次用量未知时，聚合用量也为None，不把未知算零，不记录费用估算。

任何后续批次失败都拒绝整个结果，停止后续请求；之前已经成功的外部请求不因此撤销，不能把“无向量结果”解释为“未产生供应商用量”。完整结果JSON另限2 MiB，含全部来源/向量/覆盖字段；超限拒绝整体，不丢片、切JSON或返回部分成功。

| 错误码 | 含义 |
|---|---|
| `embedding_not_configured` / `embedding_config_invalid` | 缺少独立配置或调用时配置不符合契约 |
| `embedding_input_invalid` / `embedding_input_budget_exceeded` | 输入/来源校验失败或预算超限，尚未发送首批 |
| `embedding_request_failed` / `embedding_timeout` | HTTP/连接/未知客户端失败或超时；前批可能已完成 |
| `embedding_response_invalid` / `embedding_response_too_large` | 响应格式/映射/数值/模型/用量无效或读取超限 |
| `embedding_result_too_large` | 完整输出超限，外部请求可能已全部完成 |

以上均为固定服务错误文案，尚无应用HTTP状态映射。供应商错误体、异常文本、密钥和输入不反射；客户端之外的调用方仍须遵守已有安全边界。

## 来源与模型空间

结果保留Workspace/Task标识、文件元数据、原分块及其正文/坐标/摘要/ID，并分别记录请求模型、报告模型、预期维度（有向量时已逐条核对）、请求次数和已确认用量。来源的 `truncated` / `incomplete_reasons`、分块策略、忽略策略及解析版本原样保留；Embedding成功不能使不完整扫描变完整，也不能证明文件当前版本。

`embedding_space_id` 为协议版本、规范化配置根地址、请求模型、报告版本、维度和dimensions参数开关的SHA-256；不包含Key，Key轮换不改变空间身份。同名模型或相同维度不代表同一空间；没有请求时该ID和报告模型为None，用量也为None。该ID仅用于区分已观察的配置/版本，不证明供应商别名永远稳定或解决持久索引失效。

正文继续标记 `untrusted_project_content`；没有提示注入防御、当前授权重建、语义效果评测或索引写入承诺。整文件原始摘要与LF归一化分块摘要仍各自保持原含义。

## 验证入口

从 `apps/api` 执行：

```bash
../../.venv/bin/python -m pytest -q tests/model/test_embedding_config.py tests/model/test_code_embeddings.py
```

[72项配置测试](../apps/api/tests/model/test_embedding_config.py)覆盖可选启动、独立环境变量、严格标量/地址/密钥契约、固定错误及配置不可变；[70项客户端测试](../apps/api/tests/model/test_code_embeddings.py)使用真实HTTPX请求/响应管道与受控MockTransport，覆盖请求投影、预算、乱序索引、来源/模型空间、异常响应、超时/取消清理、后批失败和完整结果预算。不是实网供应商验收，没有访问PostgreSQL、浏览器或真实项目目录。

本次Settings增加可选字段，定向回归[原配置加载](../apps/api/tests/core/test_config.py)4项；未改动聊天客户端、Agent Loop或扫描器，不默认扩大回归。配置和客户端由教练按本课明确授权实现；工程通过不代表学习者独立掌握或真实供应商可用。
