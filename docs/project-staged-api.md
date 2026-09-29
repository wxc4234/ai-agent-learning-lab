# Task 暂存差异只读 API

`GET /workspaces/{workspace_id}/tasks/{task_id}/git/staged?binding_revision=1`

仅供本地模式使用。沿用本机 Host、来源和内部运行凭证边界，身份由服务器解析；浏览器后续通过同源 BFF 调用，不直接持有后端运行凭证。浏览器入口为同路径的 `/api/workspaces/.../git/staged`；PC 在按需打开的“文件改动”详情中显式查询。

## 请求

- 路径标识为32位小写十六进制公开ID。
- 查询只接受一次 `binding_revision`，为1～9007199254740991的规范十进制整数；不接受前导零、重复参数或额外身份/路径字段。请求正文不参与观察参数构造。
- 修订仅用于核对绑定，不是授权；服务仍联表核对项目、Task与Conversation归属，并在文件观察之后再次核对。

## 成功响应

字段为 `workspace_id`、`task_id`、`binding_revision`、固定 `scope: staged_only`、`status`、`unavailable_reasons` 与 `changes`。

- `staged_compared`：`changes`是完整数组，空数组仅表示本次HEAD与index投影相同，不证明工作树干净。
- `comparison_unavailable`：`changes`为null。原因可为 `no_head_object_observed`、`commit_object_missing`、`index_missing`，可同时存在。
- 差异条目只含 `path`、`status`、`head`、`index`。状态为added/deleted/modified/unmerged。head为null或mode/object_id；index为实际存在的stage及对应mode/object_id，删除时为空数组。
- 不返回宿主路径、数据库主键、config内容、缓存stat、树图中间结果或内部运行凭证；不把内部dataclass整体序列化。
- 最终UTF-8 JSON最多1MiB，超限整次拒绝，不截断、分页或冒充完整列表。所有响应禁止缓存。

## 错误

| HTTP | 固定分类 | 含义 |
| --- | --- | --- |
| 403 | 既有本地边界分类 | 模式、Host、来源或凭证不被允许 |
| 404 | workspace_not_accessible | 不存在或不可访问，不区分归属细节 |
| 409 | staged_observation_changed | 绑定修订或观察期间内容变化 |
| 422 | invalid_staged_input | 修订或查询参数不符合契约 |
| 422 | invalid_workspace_input | 路径参数不符合既有边界契约 |
| 422 | staged_observation_unsupported | 保守格式子集不支持或对象数据不完整 |
| 422 | staged_observation_limit | 文件读取、图遍历或比较预算超限 |
| 422 | staged_response_limit | 公开响应字节预算超限 |
| 500 | staged_read_failed | 未知读取、内部投影或未识别错误，结果未知 |

目前支持保守config/布局、SHA-1 loose对象及有界自包含pack v2/v3，与无扩展index v2；许多普通Git仓库暂不兼容。未知错误码不会直接反射，读取失败不会变成缺失或无差异。

## 只读与一致性边界

项目观察和投影不提交业务写入、不修改文件、不执行Git或授予工具能力。身份依赖仍沿用既有本机用户幂等初始化；不能宣称整个HTTP链只有SELECT。前后复核不是跨资源原子快照，无法排除所有ABA或返回后的变化。

入口：[路由与公开模型](../apps/api/app/routers/workspace/git_staged.py)、[同作用域服务](../apps/api/app/services/workspace/git/project_staged.py)、[API专项](../apps/api/tests/workspace/git/test_staged_api.py)。测试使用实际本地中间件/身份依赖及工作空间路由、隔离PG与临时对象目录，不启动完整应用生命周期或浏览器。

## 绑定发现与同源代理

`GET /workspaces/{workspace_id}/tasks/{task_id}/git/binding` 只返回相同资源ID、当前binding_revision与bound布尔值，不读取目录或暴露宿主路径。PC每次主动查询先取得修订，再读取暂存差异；后一步仍重新授权，修订变化返回409，不自动重试。

BFF注入服务器运行凭证、禁止重定向与缓存，对响应正文逐块计费（1MiB）并严格解码UTF-8。固定资源/修订/状态及条目语义不符则返回502；浏览器取消为499、代理超时为504。取消覆盖响应正文读取，不把错误文本透传给用户。

PC结果只在当前Task有效；关闭详情/切换Task时取消请求并失效迟到结果。取消或失败清除旧差异，不显示“无改动”。当前展示为路径与分类，不含补丁正文。

## pack读取边界

对象优先读loose；缺失时在相同config作用域内完整解码自包含pack，支持OFS_DELTA和REF_DELTA。pack文件名校验和、全包SHA-1、对象封装及增量链均核对，保留文件观察器到第二次授权后。缺失commit现用 `commit_object_missing`，不再声称pack未查。

最多4个pack、单包16MiB、累计压缩32MiB、对象正文累计8MiB/4000个对象；每包另有2000对象、单对象1MiB、增量深度32的限制。超预算拒绝，不读取外部thin-pack基对象，不依赖idx/MIDX作授权或哈希证据。叶对象存在性仍未在暂存图比较中逐项检查。
