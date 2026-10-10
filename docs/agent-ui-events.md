# Agent UI 事件设计

| 后端事件 | 前端状态 | UI 行为 |
|---|---|---|
| `RUN_STARTED` | `thinking` | 禁用发送按钮，显示“正在思考” |
| `RUN_CANCELLATION_REQUESTED` | 保持当前状态 | 记录用户停止或超时原因，等待流实际终止 |
| `TOOL_CALL_START` | `thinking` | 内部记录工具调用；产品不直接展示工具名、原始参数或调度协议 |
| `TOOL_CALL_RESULT` | `thinking` | 内部记录结果和耗时；后续在答复/改动中呈现必要的可读来源 |
| `TOOL_CALL_ERROR` | `thinking` | 内部标记工具失败，允许模型根据错误继续决策；不暴露原始错误协议 |
| `TEXT_MESSAGE_START` | `streaming` | 创建空的助手消息气泡 |
| `TEXT_MESSAGE_CONTENT` | `streaming` | 按到达顺序追加文本片段 |
| `TEXT_MESSAGE_END` | `streaming` | 结束本条消息的流式显示 |
| `RUN_FINISHED` | `done` | 恢复输入框，保存并展示步骤数、Token、费用和耗时摘要 |
| 用户点击停止或请求被取消 | `aborted` | 保留已生成片段，标记本次运行已停止 |
| `RUN_ERROR` | `error` | 显示可读错误和重试按钮；预算耗尽与 usage 未知使用稳定错误码 |

## PC信息层级

侧边栏底部提供[模型设置](model-settings.md)：聊天服务与可选代码检索服务分别配置，聊天没有向量维度，检索支持检测默认维度。此处管理用户自己的连接配置，不提供批次/空间/查询调试操作。

默认显示项目/任务导航与对话，顶部只有项目名和“查看改动”。产品不提供高级详情、手动向量批次/模型空间选择、Embedding查询预览、哈希/上下文包或技术诊断入口；这些属于[内部检索链路](code-context.md#内部查询校验与产品边界)，不以默认折叠或“高级”菜单继续交给用户操作。用户提供任务意图，后续由Agent内部检索；必要时在答复或文件改动中呈现易读的文件/行号，而不是要求用户理解工程协议。本地流式Agent已按请求注册search_code，模型可检索宿主预先保存的代码快照；没有自动扫描/生成索引。回答中的路径/行号仅为可读引用，尚无点击文件跳转。

文件改动按需打开，保持已有提案审阅/显式审批和实际执行结果；不开常驻复杂右栏，不展示缺少真实数据的分支/变更统计或来源。内部实验组件保留为对照，ChatPanel没有导入/挂载这些组件，WorkbenchShell与provider也没有advanced详情模式。对话用量摘要保留原有行为，其只读恢复请求不属于诊断面板读取。

回复下方不显示运行简报、工具调用次数或执行详情按钮；审阅提案从顶部查看改动进入，不提供内部运行记录导航。运行完成不等于每项验证通过。打开详情聚焦关闭按钮，Escape/关闭后焦点返回查看改动；开合不重建对话或丢失草稿。批准后的提案详情提供默认折叠的“应用到受控样例”；用户仍需勾选确认并单独点击应用，待审批/已拒绝时不显示入口。服务端重新授权并核对执行目标；普通项目提供独立的显式应用和审计/恢复入口。提案审批/应用授权与防重复机制沿用既有组件，不因收起面板而自动执行或重试。普通项目许可区查询到记录且没有未确认变更时，可显式点击“检查写入条件”；展示检查时的结果，应用时仍须重新复核。检查可取消，失败显示未知；收起许可区/详情、切换提案或重新查询许可都会清除旧诊断，不自动检查或重试。

当前产品浏览器入口见下节。`workbench-simple.mjs`的联合夹具已同步撤下内部入口的预期，本次未复跑其真实BFF/审批/聊天持久化流程。依赖`workbench-navigation.mjs`高级详情的旧专项属于历史工程证据，不是当前产品回归入口。

## 当前产品界面验收

代码检索已通过[PC真实联合专项](code-context.md#代码检索pc联合验收)：命中回答展示实际工具来源的路径/行号，窗口未找到与检索失败分别说明，停止和刷新不重放；1366/1920视口通过。模型出口受控，点击文件跳转与真实语义尚未验收，详细证据及复跑入口集中在检索协议。

[产品工作台专项](../apps/web/test/browser/workbench-product/verify.mjs)8组通过；实际ChatPanel、WorkbenchShell、WorkspaceSidebar、TaskChangesPanel与生产样式使用受控BFF响应，核对1366/1920宽度、内部组件在DOM中不存在且没有检索/诊断流量、文件改动键盘开合/调宽、草稿/焦点、Task/Workspace切换、旧改动回执隔离和刷新。零模型或变更请求，无pageerror；截图已人工检查，报告位于忽略目录`apps/web/output/playwright/workbench-product/`。没有真实Next/API/数据库联合或聊天发送/文件写入验收，不代表真实模型语义或Windows可用。

改动涉及工作台入口、ChatPanel挂载和provider中无用的详情模式，回归只覆盖其直接影响的导航/文件改动/草稿生命周期；未修改后端、BFF、Agent reducer或文件写入实现，不扩大全量/账号专项。受影响TypeScript/ESLint及空白检查通过。核心和配套由教练按当课明确授权完成，不代表独立掌握。

在apps/web运行，Playwright/Chrome配置沿用[环境入口](../ENVIRONMENT.md#5-定向测试与静态检查)：

```bash
node test/browser/workbench-product/run.mjs
pnpm exec eslint src/features/chat/components/chat-panel.tsx \
    src/features/workbench/components/workbench-shell.tsx src/features/workbench/workbench-session.tsx \
    test/browser/workbench-product/run.mjs test/browser/workbench-product/entry.tsx \
    test/browser/workbench-product/verify.mjs test/browser/workbench-simple.mjs
```

定向类型检查保留实际别名与导入图，包含产品入口和内部实验的受影响模块：

```bash
node --input-type=module <<'JS'
import ts from "typescript";
const root = process.cwd();
const source = ts.readConfigFile("tsconfig.json", ts.sys.readFile);
const config = {
    ...source.config,
    compilerOptions: { ...source.config?.compilerOptions, incremental: false, typeRoots: [root + "/node_modules/@types"] },
    include: [
        "src/features/workbench/code-query-context-request.ts",
        "src/features/workbench/components/code-query-context-preview.tsx",
        "src/features/workbench/components/code-batch-summaries-panel.tsx",
        "test/features/workspaces/code-query-context-request.test.ts",
        "test/browser/workbench-product/entry.tsx",
    ],
};
const parsed = ts.parseJsonConfigFileContent(config, ts.sys, root);
const errors = [
    ...(source.error ? [source.error] : []), ...parsed.errors,
    ...ts.getPreEmitDiagnostics(ts.createProgram({ rootNames: parsed.fileNames, options: parsed.options })),
];
if (errors.length) console.error(ts.formatDiagnosticsWithColorAndContext(errors, {
    getCanonicalFileName: name => name, getCurrentDirectory: () => root, getNewLine: () => "\n",
}));
process.exitCode = errors.length ? 1 : 0;
JS
```

## 内部展示实验与历史证据

下述工具结果模块及专项保留协议/工程证据，当前不挂载到产品高级详情，也不作为用户操作入口。依赖旧诊断入口的浏览器脚本不属于当前产品回归；已有解析/协议测试与后端能力仍独立有效。后续需要来源呈现时，应先设计用户能读懂的任务结果，再复用必要的安全投影，不重新开放整套调试台。

## Vault 检索展示

本地聊天输入框在发送前说明代码/笔记检索的查询和命中片段会提供给用户配置的相应模型。`search_vault` 沿用聊天流；内部展示实验的实时工具结果与历史 Run 共用卡片，展示查询、读取量、相对路径/命中行列、纯文本片段和默认折叠的完整文件 SHA-256。引用仅是来源文本，当前不提供点击读取或文件跳转。

解析先限制完整 UTF-8 JSON 为64 KiB，再核对 `source=authorized_vault`、`content_trust=untrusted`、当前 Workspace/Task、字段与预算、规范化 Markdown 路径及引用坐标。Python 列号按 Unicode 码点计数，浏览器使用 `Array.from` 校验片段内命中，避免 emoji 导致 UTF-16 偏移。未知协议或范围不符只显示无法确认，不退回原始结果；资料不解释为 HTML、Markdown、URL 或操作指令。

完整无匹配仅描述本次支持范围；覆盖不完整保留清单、20文件或50命中行预算原因。片段裁剪另行说明，不能据此推断检索覆盖不完整；工具错误不能当作没有笔记。引用及摘要只对应当次读取，历史只查询已存事件，不重新检索或承诺当前版本。命中列表和片段可键盘聚焦/滚动，摘要用原生折叠控件。

协议与实际组件测试见[Vault 展示专项](../apps/web/test/features/chat/vault-search.test.ts)，PC 联调入口见[隔离环境](../ENVIRONMENT.md#5-定向测试与静态检查)；受控模型不能证明真实模型措辞或提示注入防御质量。

## Day 5 状态约束

- 新请求从 `idle`、`done`、`aborted` 或 `error` 进入 `thinking`。
- 工具事件不会直接改变整次运行的终态；只有 `TEXT_MESSAGE_START` 到达后才从 `thinking` 进入 `streaming`。
- 正常结束只能进入 `done`；用户取消只能进入 `aborted`；网络、超时、500、502 或限流只能进入 `error`。
- `error` 重试会清空半截回答，但复用原问题和会话标识，避免在 UI 中追加重复回答。

## 传输协议

`POST /chat/stream` 使用 `application/x-ndjson`：每个事件是一行独立 JSON。前端必须先按换行重组网络分块，再解析事件，不能假设一次 `reader.read()` 恰好得到一条完整事件。

`TOOL_CALL_RESULT.duration_ms` 为单次成功执行的非负整数耗时。`TOOL_CALL_ERROR.duration_ms` 在执行异常或超时时为非负整数，在未知工具或参数校验失败时为 `null`；字段不会省略。浏览器解析器会按错误代码校验这一阶段语义，拒绝缺失字段、非法整数和错误的空值。聊天状态中的 `durationMs` 在工具运行时不存在，结束后原样保存为整数或 `null`。

`max_steps_exceeded`、`token_budget_exhausted`、`token_usage_unknown` 三种 Agent Loop 终态的 `RUN_ERROR` 会额外携带 `steps_taken` 与和成功事件同结构的 `metrics`，用于复核失败前已经产生的成本；其他模型、网络或服务错误仍可只有 `code` 与 `message`。

浏览器解析器要求 `steps_taken` 与 `metrics` 同时出现或同时缺失；完整失败摘要复用 `RUN_FINISHED` 的字段校验。完整失败摘要写入聊天状态，并可从持久化 Run 终态事件恢复。

## 模型文本增量与保存

`/chat/stream` 使用 `StreamingDeepSeekDecisionMaker` 发起 `stream=True` 请求。公开正文 delta 经 Runtime 的 `ModelTextDelta` 原样转为 NDJSON 文本事件，Markdown 随片段更新；不发送隐藏推理或未完成的工具参数。工具 id/name/arguments 按索引拼接，明确 `tool_calls` 终态且流完整结束后，才进入现有注册表、参数校验与预算门禁。一次仍只支持一个工具调用。

同一 Run 中不同模型步骤的公开文本用空行分隔。增量片段只用于实时传输；成功结束后把完整可见文本一次保存为助手消息，并记录一份完整 Run 文本，不逐片段写数据库。历史恢复直接显示完整消息，不重播打字机效果。取消、断流或截断时保留页面已显示片段并进入失败/取消态，不保存为完整会话回答、不执行不完整工具、不自动重试。

模型调用结束后的 usage 用于指标与续跑预算；缺失值保持未知。每步公开文本与工具字段合计最多 1 MiB，缺少明确终态的 EOF 不算成功。关闭浏览器消费或取消请求时，嵌套生成器显式关闭上游流。非流式 `/chat` 保留兼容入口。

## 流式阅读位置

发送或重试时，将本轮问题定位到对话滚动区顶部，并为本轮内容预留至少一屏可用高度。后续文本增量和终态不触发再次定位；关闭该滚动区的浏览器自动滚动锚定，用户上翻历史时保持阅读位置。窗口尺寸变化只调整预留高度，切换任务后的历史展示不重播发送定位。

## 错误提示

前端不直接展示后端原始异常：429 显示“请求太频繁了”，502 显示“模型服务暂时不可用”，其他 5xx 显示“服务暂时出错”，网络断开和超时分别给出检查网络、稍后重试的提示。

正式聊天的 Token 续跑预算由后端 `AGENT_MAX_TOTAL_TOKENS` 控制，浏览器不能覆盖。`token_budget_exhausted` 表示已知累计用量达到预算，`token_usage_unknown` 表示模型没有返回 usage，Runtime 为避免未知成本而停止继续执行。

## Git 样例差异展示

实时工具结果与历史运行详情共用 Git diff 卡片。仅识别 git_sample_diff 的完整 task_git_sample 协议：worktree 为暂存区到工作区，staged 为 HEAD 到暂存区；不含未跟踪文件，忽略子模块。卡片明确这是临时样例，不代表用户项目；空结果只表示所选范围无差异。

渲染前核对比较范围、UTF-8字节数与预算，协议不匹配时保留原始文本并提示格式未识别，不伪装为有效或空差异。正文用可聚焦、独立滚动的纯文本区域展示，不执行HTML/Markdown，不增加应用补丁操作。工具错误沿用失败事件，不显示成功diff卡片。刷新后读取已有Run事件展示同一结果，不重新调用Git或模型。

验收入口为 [Git diff浏览器脚本](../apps/web/test/browser/git-diff.mjs)；启动方式见 [环境说明](../ENVIRONMENT.md#5-定向测试与静态检查)。

## 受控样例验证展示

`verify_task_sample` 的实时结果与历史详情共用 `ToolResult`、严格协议解析器和验证卡片。卡片注明固定目标与受控快照范围，展示通过/失败/未确认、进程退出事实、报告状态及七项计数；工具调用完成不等于验证通过。

解析器按公开契约限制 4096 UTF-8 字节、固定来源/计划/字段和计数范围，并按退出事实与报告复算结论。缺失报告不显示为零测试；未知、截断或零成功不显示通过。非法协议只显示无法确认的提示，不渲染原始内容。历史读取不执行工具，卡片没有重试入口。

验收入口为 [验证展示浏览器脚本](../apps/web/test/browser/verification.mjs)与[解析测试](../apps/web/test/features/chat/verification.test.ts)。浏览器覆盖真实 BFF、流与 PostgreSQL 历史，模型/工具结果采用展示夹具，不代替真实 Sandbox 执行验收。

## 应用样例差异展示

`read_task_sample_diff` 使用独立卡片，固定来源 `task_application_sample`、比较方式 `fixed_old_to_snapshot` 与文件 `example.txt`，与 `git_sample_diff` 的独立仓库来源分开识别。实时与历史共用严格解析，展示固定 old+LF 基线、基线/快照 SHA-256、字节数与纯文本差异；摘要不是验证通过或后续版本承诺。

校验固定基线摘要、字段集合、UTF-8往返和字节数，公开JSON最多1MiB、差异最多256KiB。非法结果只显示无法确认提示，不退回原始大文本；空差异不意味着项目干净或测试通过。正文可键盘滚动，不解释HTML/Markdown，不提供应用或重试按钮。历史只读取已保存结果。

[解析测试](../apps/web/test/features/chat/task-sample-diff.test.ts)与[浏览器入口](../apps/web/test/browser/task-sample-diff.mjs)提供验收；后者通过真实BFF/流/隔离PostgreSQL，模型与工具结果为展示夹具，不验证Git/Docker执行。

改动详情内提供按需查询的变更组与隔离工作区：展示完整 Diff 后显式批准、应用或恢复；隔离副本导出只生成原任务待审批变更。刷新不重放未知应用。协议见[普通项目执行](project-execution.md)。
