# ChatSearch：双答案代码级状态机

这一组问题的核心不是再说一遍“状态机 + 幂等”，而是说明：**数据归属由什么字段决定、状态按什么顺序更新、谁有资格执行整轮副作用。**

> **阅读边界：**字段、路由和主要收口链路来自【当前实现】；文中明确标为“更强写法”或“应增加”的入口守卫属于【改进建议】。

<details>
<summary><strong>展开查看：双答案字段、完成条件、竞争处理和代码级伪代码</strong></summary>

## 1. 每个 SSE 包如何确定写入 A 还是 B

双答案共用一条 SSE。服务端在每个分支包上携带 `compareIdx`：

```text
compareIdx = 0 → 答案 A
compareIdx = 1 → 答案 B
```

这里存在两级路由。

### 第一级：ChatStream 数据层路由

每个 SSE 回调已经绑定发起时的 `QAPair.id`，所以先确定属于哪一轮，再根据 `compareIdx` 转换成答案 `aid`：

```typescript
function onSseMessage(data, qAPair) {
    const branchIndex = data.options.compareIdx;
    const aid = branchIndex + 1;

    updateQAPair({
        id: qAPair.id,  // 先固定轮次
        aid,            // 再固定 A/B 分支
        data,
    });
}
```

这样即使用户已经发起下一问，旧 SSE 的包也只能更新旧 QAPair，不会根据当前页面索引写入新轮次。

### 第二级：Generate 渲染层路由

组件收到数据后，再计算目标分支：

```typescript
function handleMessageData(data, targetIndex?) {
    const {options = {}, content = {}} = data;

    // comparison 首包到达时进入对比模式，并补齐 A/B 槽位
    if (options.comparison && compareInfo.mode !== 'compare') {
        activateCompareMode(options.comparison);
        initCompareAnswerSlots();
    }

    // 协议字段优先，targetIndex 是上层明确指定，currentIndex 只能作为非对比兜底
    const branchIndex = options.compareIdx
        ?? targetIndex
        ?? currentIndex;

    // 异常 compareIdx 直接丢弃，不能写入用户当前浏览列
    if (branchIndex < 0 || branchIndex >= answerList.length) {
        return;
    }

    const sectionIndex = getLatestAnswerSectionIndex(branchIndex);

    // 停止后到达的晚包不再进入渲染链路
    if (answerList[branchIndex][sectionIndex].sseStatus === 'ABORT') {
        return;
    }

    routeToBranch(branchIndex, sectionIndex, content);
}
```

必须明确：

- `compareIdx`：网络包归属，决定写 A 还是 B；
- `currentIndex`：当前展示哪一列，用户切换时会变化；
- `viewingIndex`：状态机记录的浏览列；
- `selectedIndex`：最终选择列。

**用户切列只能影响展示，不能影响 SSE 数据归属。**

## 2. 维护了哪些网络状态、渲染状态和交互状态

### 网络状态

| 字段 | 粒度 | 含义 |
|---|---|---|
| `QAPair.id` | 对话轮次 | 将异步 SSE 回调绑定到稳定业务实体 |
| `answerList[i][j].sseStatus` | 分支 Section | `WAITING/GENERATING/COMPLETE/ABORT` |
| `compareSseEndedMap[i]` | 分支 | 是否收到该分支的 `endTurn` |
| `options.state` | SSE 包 | 是否为 `generate-complete` 包 |
| `options.endTurn` | SSE 包 | 当前分支是否停止继续发包 |
| `options.qid` | 服务端回答 | 用于回答关联和续传 |
| `seq_id/checkpoint` | 数据包 | 断点续传位置，不参与 DOM 完成判断 |

需要区分两个“完成”概念：

```text
包级完成：state=generate-complete && generator.isFinished
分支网络结束：endTurn=true，并写入 compareSseEndedMap[compareIdx]
```

不能只看 `generator.isFinished`，因为某个 Markdown 段完成不代表整个回答分支完成。

### 渲染状态

| 字段 | 粒度 | 含义 |
|---|---|---|
| `answerList[i][j].isFinished` | 分支 Section | AIEntry 已完成可视渲染 |
| `answerList[i][j].isComplete` | 分支 Section | 分支级完成逻辑已经执行 |
| `queue` | AIEntry | 尚未消费的 Block |
| `prevBlockRenderFinished` | AIEntry | 当前包是否已 ack，可否消费下一包 |
| `lastBlockRender` | AIEntry | 是否已经收到结束 Block |
| `isRenderFinishedDispatched` | AIEntry | 整个 Entry 的完成事件是否已经派发 |
| `block._renderFinished` | 单 Block | 防止一个组件重复 ack 同一包 |

这里 `isFinished` 和 `isComplete` 不能合并：

- `isFinished` 回答“DOM 是否渲染完”；
- `isComplete` 回答“分支完成副作用是否处理过”。

### 交互状态

| 字段 | 含义 |
|---|---|
| `compareInfo.mode` | `normal/compare` |
| `compareInfo.state` | `generating/awaiting_selection/selected` |
| `compareInfo.viewingIndex` | 用户当前浏览列 |
| `compareInfo.selectedIndex` | 用户最终选择列，未选择为 `-1` |
| `currentIndex` | 当前组件的展示投影，不作为网络路由依据 |
| `compareAbortedByUser` | 是否由用户主动停止，影响选择结果是否上报 |

## 3. 判断“整轮完成”的条件是什么

预期分支数来自 `comparison.labels.length`，没有配置时按双列兜底。判断顺序如下：

```typescript
function isCompareAllDone({compareInfo, compareSseEndedMap, answerList}) {
    // 1. 必须处于对比模式
    if (compareInfo.mode !== 'compare') {
        return false;
    }

    const expectedBranches = getCompareBranchCount(compareInfo);

    // 2. Network Barrier：每个分支都收到 endTurn
    const allSseEnded = range(expectedBranches)
        .every(i => compareSseEndedMap[i] === true);
    if (!allSseEnded) {
        return false;
    }

    // 3. 分支槽位必须已经完整创建
    if (!answerList || answerList.length < expectedBranches) {
        return false;
    }

    // 4. Render Barrier：每个分支最后一个 Section 都 settled
    const allSettled = range(expectedBranches).every(i => {
        const branch = answerList[i];
        const lastSection = branch[branch.length - 1];

        return lastSection.isFinished === true
            && lastSection.sseStatus !== undefined
            && lastSection.sseStatus !== 'GENERATING';
    });
    if (!allSettled) {
        return false;
    }

    // 5. once 守卫：已经收口过就不能再次进入
    if (compareInfo.state === 'awaiting_selection') {
        return false;
    }

    return true;
}
```

最终公式是：

```text
整轮完成
= compare 模式
&& 所有分支 endTurn
&& 分支数量完整
&& 所有分支 DOM isFinished
&& 所有分支 sseStatus 已进入终态
&& 当前尚未执行过整轮收口
```

### 分支数不是直接硬编码 `0、1`

前端会先从服务端下发的对比配置中取得预期分支数：

```typescript
function getCompareBranchCount(compareInfo) {
    return compareInfo.comparison?.labels?.length || 2;
}
```

因此默认双答案时，下面的遍历结果确实是索引 `0、1`；但判断写成了按 `expectedBranches` 遍历，而不是直接写死：

```typescript
const allSseEnded = Array.from({length: expectedBranches})
    .every((_, branchIndex) => compareSseEndedMap[branchIndex] === true);
```

如果未来协议返回三个候选答案，并且 `labels.length=3`，相同逻辑会检查 `0、1、2`。

### 前端并不知道服务端“一定会返回”所有 endTurn

“对比模式下，每个分支最后都返回一个带 `compareIdx + endTurn` 的完成包”是前后端协议契约。前端只记录实际收到的信号：

```typescript
if (options.endTurn && typeof options.compareIdx === 'number') {
    compareSseEndedMap[options.compareIdx] = true;
}
```

如果 A 返回了 `endTurn`、B 没返回，则状态为：

```text
compareSseEndedMap = {0: true, 1: false/undefined}
allSseEnded = false
```

此时前端不会误判正常完成，而是继续等待；后续只能由 B 的完成包、网络异常、用户停止或超时降级路径来收敛。用户停止和异常中断时，前端会主动把未完成分支设置为 `ABORT`，并人工补齐 ended/finished，这不代表服务端真的返回了 `endTurn`，而是业务层明确进入中断终态。

当前 `generate.san` 还保留了一个兼容兜底：收到 `endTurn=true` 但没有 `compareIdx` 时，会把所有分支标记为 ended。按照现有协议“对比包必须携带 compareIdx”，这个分支理论上不应该命中。更严格的实现应当把它视为协议异常，执行上报并等待异常收口，而不是直接推断所有分支完成，否则畸形包可能提前打开 Network Barrier。

另外，`request-actions.ts` 的传输层使用 `Set<number>` 记录完成分支，并以 `set.size >= expectedBranches` 判断网络层完成。这同样依赖服务端保证 `compareIdx` 合法；如果收到 `0` 和异常索引 `7`，Set 的数量也可能达到 2。渲染层已经有索引越界检查，更稳妥的做法是传输层也先验证 `0 <= compareIdx < expectedBranches`，并最终检查预期索引集合，而不只检查 Set 大小。

这里没有强制所有分支必须是 `COMPLETE`，而是要求不能为 `GENERATING/undefined`。原因是用户停止时，未完成分支会进入 `ABORT`，它同样是一种 settled 终态。

## 4. SSE 和 DOM 同时尝试收口，怎样保证副作用只执行一次

两条路径都会尝试收口，但都只调用统一入口：

```typescript
// 路径一：SSE 先完成
function onCompleteProcess(branchIndex, sectionIndex) {
    if (section.sseStatus === 'ABORT') {
        return; // 停止已经抢先成为终态
    }

    setSseStatus('COMPLETE');

    // DOM 已经先完成，立即尝试整轮收口；否则等 DOM 回调
    if (section.isFinished) {
        completeHandle(branchIndex, sectionIndex);
    }
}

// 路径二：DOM 先完成
function onRenderFinished(branchIndex, sectionIndex) {
    section.isFinished = true;

    // 网络已经先进入终态，立即尝试整轮收口；否则等 SSE 回调
    if (section.sseStatus === 'COMPLETE'
        || section.sseStatus === 'ABORT') {
        completeHandle(branchIndex, sectionIndex);
    }
}
```

统一入口先处理分支，再判断整轮：

```typescript
function completeHandle(branchIndex, sectionIndex) {
    finishBranchAnswer(branchIndex, sectionIndex);

    if (isCompareAllDone(currentState)) {
        finishAllAnswers(branchIndex, sectionIndex);
    }
}

function finishBranchAnswer(i, j) {
    answerList[i][j].isComplete = true;

    if (answerList[i][j].sseStatus === 'ABORT') {
        addDefaultInteract(i, j);
    }
    else {
        // false 表示只完成当前分支，不触发整轮完成通知
        sseSuccessEnd(false, i, j);
    }
}
```

真正的整轮副作用只能从 `completeCompareAnswer()` 进入：

```typescript
function completeCompareAnswer() {
    if (!isCompareAllDone(currentState)) {
        return;
    }

    sendFinishedToMain();       // answer-end
    updateGlobalStatus('COMPLETE');
    hideGeneratingUI();
    markCompareAwaitingSelection();
}
```

保证只执行一次依赖三层防线：

1. **Block 级**：`block._renderFinished` 防止一个组件重复确认；
2. **分支级**：`isComplete` 记录分支完成逻辑已经执行；
3. **整轮级**：第一次收口后把 `compareInfo.state` 改成 `awaiting_selection`，下一次 `isCompareAllDone()` 直接返回 false。

浏览器 JavaScript 回调是串行执行的。当前 `completeCompareAnswer()` 中没有 `await`，所以两个异步任务不会执行到一半互相穿插：第一个回调完成状态转换后，第二个回调才有机会进入，此时 once 守卫已经关闭。

日志的收口也在同一条链路中：`sendFinishedToMain()` 触发一次 `answer-end`，`AnswerLogger.onAnswerEnd()` 才发送一次 `ans_content_finish`。分支完成只调用 `sseSuccessEnd(false)`，不能触发整轮日志、已读或反馈类副作用。

普通单答案完成链路还使用 `hasSendEnd` 保护完成日志、反馈卡和已读通知。双答案如果要展示反馈卡，也必须把它放在 `completeCompareAnswer()` 的 once 守卫之后，禁止放在 `finishBranchAnswer()` 中。

### 更强的防同步重入写法

当前实现依赖“同步函数执行完再处理下一个任务”。如果还要防止同步事件派发造成重入，我会先抢占终态，再执行副作用：

```typescript
function tryFinalizeCompareRound() {
    if (compareInfo.state !== 'generating') {
        return;
    }
    if (!allNetworkEnded() || !allDomSettled()) {
        return;
    }

    // compareInfo.state 相当于 compare-and-set 的 once token
    markCompareAwaitingSelection();

    // 抢占成功以后才能执行整轮副作用
    emitAnswerEnd();
    sendFinishLog();
    showFeedbackCardOnce();
    updateGlobalStatus('COMPLETE');
}
```

## 5. “停止生成”和“正常完成”竞争时，最终进入什么状态

首先要区分网络完成和业务完成：

```text
网络 COMPLETE：分支收到 endTurn
业务 COMPLETE：网络完成 + DOM 完成 + 整轮 once 收口完成
```

如果网络刚结束但 DOM 还没有完成，此时用户点击停止，业务上仍然可以判定为“停止生成”，因为用户看到的回答尚未完成。

### 正常完成入口的判断顺序

```typescript
function onCompleteProcess(i, j) {
    const currentStatus = answerList[i][j].sseStatus;

    // stop 已经先提交 ABORT，晚到的 COMPLETE 无权覆盖
    if (currentStatus === 'ABORT') {
        return;
    }

    answerList[i][j].sseStatus = 'COMPLETE';

    if (answerList[i][j].isFinished) {
        completeHandle(i, j);
    }
}
```

### 停止入口的判断顺序

```typescript
function stopCompareByUser() {
    for (const branch of answerList) {
        const section = getLastSection(branch);

        // 已经完成分支级收口的列保留 COMPLETE
        if (!section.isComplete && section.sseStatus !== 'ABORT') {
            section.sseStatus = 'ABORT';
        }

        stopAiContainer(section);
    }

    updateGlobalStatus('ABORT');
    compareAbortedByUser = true;

    // 先关闭正常完成 once gate
    compareInfo = toCompareAwaitingSelection(compareInfo);

    // 中断时部分组件不会再发 render-finished，必须人工补齐 settled 状态
    for (const branch of answerList) {
        const section = getLastSection(branch);
        compareSseEndedMap[branch.index] = true;
        section.isFinished = true;
        section.isComplete = true;
        section.isLoading = false;
    }
}
```

### 最终状态表

| 谁先取得业务终态 | 分支 `sseStatus` | 全局状态 | `compareInfo.state` | `compareAbortedByUser` |
|---|---|---|---|---|
| 两列网络和 DOM 全部完成 | 两列 `COMPLETE` | `COMPLETE` | `awaiting_selection` | `false` |
| 用户在业务完成前停止 | 未完成列 `ABORT`；已完成列可保持 `COMPLETE` | `ABORT` | `awaiting_selection` | `true` |
| 用户完成偏好选择 | 保留原终态 | 已收口 | `selected` | 保留原值 |

正常完成和停止生成最后都会进入 `awaiting_selection`，因为用户仍然需要浏览或选择答案；但是网络状态、全局状态和 `compareAbortedByUser` 不同，所以后续日志和选择上报行为也不同。

### 到底由谁决定胜负

不是 SSE 回调单独决定，也不是 DOM 回调单独决定，而是业务层的终态提交规则决定：

1. JavaScript 事件循环让两个回调串行执行；
2. `ABORT` 一旦先写入，晚到的 `onCompleteProcess()` 会直接返回；
3. 只有 `isComplete=true` 的分支才被视为已经完成业务收口；
4. 用户停止会先把 `compareInfo.state` 推进到 `awaiting_selection`，关闭正常完成的 once gate；
5. 如果整轮正常完成已经抢先进入 `awaiting_selection`，后续停止事件应直接忽略。

最后一条可以用更明确的入口守卫表达：

```typescript
function stopCompareByUser() {
    // 正常完成或已经选择后，不允许迟到的 stop 覆盖终态
    if (compareInfo.state !== 'generating') {
        return;
    }

    // ...提交 ABORT
}
```

当前实现已经通过 `ABORT` 检查、`isComplete`、`awaiting_selection` 幂等守卫和隐藏停止按钮处理主要竞争；入口再增加上述状态判断，可以把“谁先取得终态谁生效”的规则表达得更完整。

## 6. 一段完整的面试回答

> 每个包先通过 QAPair.id 确定轮次，再通过 options.compareIdx 确定 A/B，compareIdx 的优先级高于用户当前浏览的 currentIndex。网络侧我维护每列的 sseStatus 和 compareSseEndedMap，渲染侧维护 isFinished、isComplete 以及 AIEntry 的 queue/ack 状态，交互侧统一放在 compareInfo 中，包括 generating、awaiting_selection、selected、viewingIndex 和 selectedIndex。
>
> 整轮完成按固定顺序判断：处于 compare 模式、所有分支 endTurn、answerList 数量完整、每列最后一个 Section 的 DOM isFinished，并且 sseStatus 已经进入 COMPLETE 或 ABORT 等终态，最后还要求 compareInfo 尚未进入 awaiting_selection。SSE 和 DOM 谁后到，谁都会调用同一个 completeHandle；最终只有 completeCompareAnswer 能发 answer-end。第一次收口后把状态切到 awaiting_selection，后续调用就被幂等守卫拒绝。日志由 answer-end 驱动，反馈卡也只能放在这个整轮 once 区域，不能放在分支完成中。
>
> 停止和正常完成竞争时看谁先提交业务终态。stop 先写 ABORT，晚到 COMPLETE 会被 onCompleteProcess 拒绝；正常完成已经使分支 isComplete、整轮进入 awaiting_selection 后，迟到 stop 应被忽略。如果只是网络结束但 DOM 未完成，isComplete 仍为 false，此时用户停止可以把未完成分支收敛成 ABORT。正常和停止最终交互态都可能是 awaiting_selection，但全局状态和 compareAbortedByUser 不同，用来决定日志与选择结果是否上报。

</details>

---
