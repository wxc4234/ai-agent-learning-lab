# ChatSearch：SSE 中断与断点续传

> **难点一句话：**SSE 回调是异步到达的，页面“当前轮次”却会随用户操作立即变化；网络重连还涉及确认点、去重与序号缺口，因此必须同时解决旧请求污染和续传一致性。

> **阅读提示：**本章重点讲【当前实现】如何隔离旧回调和计算续传序号；checkpoint 是否真正不重不漏，请直接看“深挖 B”。

## 1. 业务背景

AI 回答持续时间可能很长。期间用户可能停止生成、快速发送下一问，或者从 Wi-Fi 切到移动网络。项目不能假设一条 SSE 从开始到结束永远稳定，也不能假设回调一定按照发起顺序返回。

## 2. 难点具体发生在哪里

### 问题一：旧请求回调污染新轮次

典型竞态如下：

```text
T1：创建 QAPair-A，开始 SSE-A
T2：用户立即发送问题 B
T3：停止 SSE-A，创建 QAPair-B，currentQAPairIndex 指向 B
T4：SSE-A 的 abort/message 回调才到达
T5：如果回调按 currentQAPairIndex 更新，A 的状态和数据被写进 B
```

`abort()` 只能发出中断信号，不能保证在调用瞬间所有已进入任务队列的回调都消失。因此“已经停止旧请求”不等于“旧请求再也不会回调”。

### 问题二：断点续传存在 off-by-one 边界

协议中有两个含义不同的序号：

```text
maxSeqId：当前实现中，SSE 层已经完成 JSON 解析并见到的最大包序号
checkpoint.seq_id：服务端下一次应该返回的包序号
```

假设 `0、1、2` 都已经成功提交到业务 Store，则已提交序号为 `2`，下一次应该从 `3` 开始。如果误从 `2` 开始，服务端会重放最后一个包；如果误从 `4` 开始，会丢失第 3 包。这里的关键并不是简单做 `+1`，而是作为基准的序号必须代表“连续且已成功提交”，不能只代表“网络层见过”。

### 问题三：中断原因不能混为一种错误

以下情况虽然最终都可能触发 abort，但业务含义完全不同：

| 类型 | 是否错误 | 页面行为 |
|---|---|---|
| 用户点击停止 | 否 | 保留已生成内容，展示停止态 |
| 新问题打断旧请求 | 否 | 旧轮次收口，新轮次继续 |
| 服务端要求 reconnect | 可恢复 | 使用 checkpoint 继续原轮次 |
| 请求建立前失败 | 是 | 展示错误或重试入口 |
| 生成过程中网络错误 | 是/可重试 | 先续传，超过上限再失败 |
| 服务端正常 complete | 否 | 正常执行回答完成链路 |

如果全部当作普通 abort，不是误报错误，就是会把可恢复请求直接终止。

## 3. 为什么这个问题难

它跨越了三层：

- **传输层**：SSE 连接、`AbortController`、网络异常；
- **协议层**：`qid`、`seq_id`、`checkpoint`、`reconnect`；
- **业务状态层**：QAPair、答案分支、当前会话和页面展示。

只在某一层修复是不够的。例如传输层自动重连只能重新建立连接，但它不知道应该恢复哪一轮、从哪一包继续，也不知道旧回调应该写入哪个业务对象。

## 4. 解决方案

### 第一层：异步任务绑定稳定业务实体

每次发起对话先创建稳定的 QAPair id。创建 SSE 回调闭包时，把这个 id 一起绑定进去：

```text
SSE-A.message  → updateQAPair(id=A)
SSE-A.complete → updateQAPair(id=A)
SSE-A.abort    → updateQAPair(id=A)

SSE-B.message  → updateQAPair(id=B)
```

回调到达后，通过 id 在 `chatStreamData` 中重新查找目标索引，不读取可能已经变化的 `currentQAPairIndex`。这相当于把“更新页面当前位置”改成“更新这个异步任务所属的业务实体”。

对双答案还要进一步绑定 `aid/compareIdx`，确保回调不仅找到正确轮次，还能找到正确答案分支。

### 第二层：显式维护当前 SSE 实例

Store 中保存当前请求实例和它所属的 QAPair。发起新问题时先标记旧请求的停止类型，再执行 `abort()`，随后才创建新请求。

即便旧回调晚到，它仍然携带旧 QAPair id，不会因为全局当前指针已经变化而污染新轮次。

### 第三层：`AbortController` 只取消传输，业务层还要做失效校验

`controller.abort()` 能够让正在等待的 fetch/reader 尽快以 `AbortError` 结束，但不能撤回已经发生的事情：

- 已经从网络读出并进入 SSE parser 的字节；
- 已经执行的 `onmessage` 和 Store 更新；
- 已经进入 `ai-entry.queue` 的 Block；
- 已经进入 Promise microtask、San `nextTick`、`setTimeout` 或 `requestAnimationFrame` 的回调；
- 组件内部已经启动的打字机、动画或异步组件加载。

因此严格设计会把“中断网络”和“使该轮业务任务失效”分开。每个异步任务都携带稳定归属和请求代次：

```typescript
type RequestToken = {
    roundId: string;      // 前端 QAPair id
    qid?: string;         // 服务端问题 id
    epoch: number;        // 这一次请求的代次
};

type RoundRuntime = {
    epoch: number;
    phase: 'running' | 'completed' | 'aborted' | 'error';
    committedSeqId: number;
    abortCommittedThrough?: number;
    finalized: boolean;
};
```

SSE 回调在写 Store 之前判断：

```typescript
function canAcceptNetwork(token: RequestToken): boolean {
    const round = store.getRound(token.roundId);
    return Boolean(
        round
        && round.epoch === token.epoch
        && round.phase === 'running'
    );
}

function canRender(task: RenderTask): boolean {
    const round = store.getRound(task.token.roundId);
    if (!round || round.epoch !== task.token.epoch) {
        return false;
    }

    if (round.phase === 'running') {
        return true;
    }

    // 产品允许停止后展示 A 在停止前已提交的残缺内容
    return round.phase === 'aborted'
        && task.seqId <= (round.abortCommittedThrough ?? -1);
}

function onSSEMessage(token: RequestToken, event: SSEEnvelope) {
    if (!canAcceptNetwork(token)) {
        return;
    }

    commitEventToRound(token.roundId, event);
    enqueueRenderTask({token, seqId: event.seq_id, block: event.data});
}
```

渲染任务不能只在入队时校验，真正消费以及每个 `await` 之后都要再次校验：

```typescript
async function consumeRenderTask(task: RenderTask) {
    if (!canRender(task)) {
        return;
    }

    await ensureDynamicComponentLoaded(task.block);

    // await 期间用户可能已经停止 A 或开始 B
    if (!canRender(task)) {
        return;
    }

    // 渲染目标必须是任务自己的 roundId，不能取 activeRound
    renderIntoRound(task.token.roundId, task.block);
}
```

用户停止 A 并开始 B 时，判断顺序是：

```typescript
function stopRound(token: RequestToken, reason: 'user-abort' | 'new-request') {
    const round = store.getRound(token.roundId);
    if (!round || round.epoch !== token.epoch || round.phase !== 'running') {
        return;
    }

    // 1. 先抢占业务终态，让后续 message/complete 立即失效
    round.phase = 'aborted';
    round.abortCommittedThrough = round.committedSeqId;

    // 2. 再取消未来的网络读取
    controllerMap.get(token.roundId)?.abort();

    // 3. 停止 A 自己的打字和组件动画
    renderer.stopRound(token.roundId, reason);

    // 4. 取消未提交、超过停止截点的任务
    renderQueue.cancel(task => (
        task.token.roundId === token.roundId
        && task.seqId > round.abortCommittedThrough!
    ));

    finalizeOnce(round, reason);
}
```

这里先写 `aborted`、再调 `abort()` 很重要，因为 abort 动作本身可能触发同步收口逻辑。如果先中断网络、后更新业务状态，旧 complete/message 可能在窗口内抢先生效。
新问题 B 会使用新 `roundId`；如果是同一 QAPair 内重试或重答，则在启动新请求时增加 `epoch`，让上一代回调失效。

已经在停止之前提交到 A 的内容可以继续显示在 A 的残缺气泡里，但不允许继续写入 B，也不允许触发 A 的正常完成日志和反馈卡。所以 `canRender()` 可以根据产品策略允许 `seqId <= abortCommittedThrough` 的任务只渲染到 A，但新网络数据必须全部拒绝。

当前项目已有两层基础保护：

1. 请求回调闭包捕获 `QAPair.id`，通过 id 查找旧轮，不依赖可变的当前下标；
2. `ai-entry.stop()` 会将状态设为 `ABORT`，`pushBlock()` 和渲染完成回调在 `ABORT` 后不再继续推进队列。

不过，当前实现还不是完整的 epoch 方案；对已进入异步加载、`nextTick` 或定时器的任务，严格保证还需要把 token 校验放到每个异步边界。面试时应该明确区分“已经上线的 QAPair/ABORT 隔离”和“可进一步强化的 epoch 失效机制”。

### 第四层：当前使用 checkpoint 恢复原轮次

服务端返回 `reconnect` 事件时，当前 SSE 使用已经解析到的 `maxSeqId`，然后中断当前连接，并发起带 checkpoint 的新请求：

```text
服务端 reconnect(qid)
        ↓
客户端记录 maxSeqId
        ↓
checkpoint = {
    qid: 原回答 qid,
    seq_id: maxSeqId + 1
}
        ↓
继续写入原 QAPair，而不是创建新回答
```

恢复请求创建 SSE 实例时，需要反向初始化：

```text
initialMaxSeqId = checkpoint.seq_id - 1
```

这样解决了恢复实例在“还没解析到新包就再次断线”时把 `maxSeqId` 重置为 `-1` 的 off-by-one 问题。但是它只保证序号计算连续，不等价于该序号对应的数据已经成功写入 QAPair；严格确认点语义见“深挖 B：checkpoint 到底确认了什么”。

### 第五层：限制重试并区分场景

重连不是无限进行：

- 普通对话使用较小重试上限，避免长期占用资源；
- 深度任务链路耗时更长，允许更高的恢复次数；
- 超过上限后才把 QAPair 标记为 ERROR/ABORT；
- checkpoint 缺少必要字段时不盲目续传；
- 无有效序号时从协议允许的安全位置兜底。

### 第六层：将中断类型映射到不同状态

我把用户停止、新问题打断、reconnect、请求建立失败、生成中失败、连接关闭和正常完成分别处理。这样 UI、日志、重试策略和回答状态都能反映真实原因。

## 5. 关键边界推演

### 连续两次断线

第一次从 `seq_id=10` 续传，恢复实例初始化 `maxSeqId=9`。如果一包都没解析到就再次断线，下一次仍然从 10 开始；如果已经解析到 10、11，则当前实现会从 12 开始。后一句只有在 10、11 都已成功提交到业务层时才是安全的。

### 新问题与旧 abort 同时发生

旧 abort 只更新旧 QAPair。新 QAPair 的状态由自己的 SSE 实例维护，不会被旧回调改成 ABORT。

### 用户主动停止

用户停止不走 reconnect，不把它当网络错误，也不能清掉已经渲染的内容；它只终止当前生成并让当前轮次进入可展示的停止态。

## 6. 我重点保证的系统不变量

- 每个异步回调只能修改发起它的 QAPair；
- `currentQAPairIndex` 只用于界面当前态，不作为异步回调的数据归属依据；
- 协议约定 checkpoint 表示“下一包”；严格实现应以“最大连续已提交序号”计算，而不是以“最大已解析序号”计算；
- 重连继续原回答，不创建重复 QAPair；
- 所有重试都有上限，所有中断都有明确分类；
- 旧请求晚到不会改变新请求的状态。

## 7. 项目价值

这套方案可靠地解决了旧请求串写新 QAPair，并降低了常见弱网重放的范围；但当前 checkpoint 确认过早、缺少统一去重与缺口检测，不能单独证明回答绝不重复、绝不缺失。严格的一致性边界需要继续下沉到业务提交层。

## 8. 面试回答口径

> SSE 可靠性最难的不是调用 abort，而是定义“哪一包真的可以确认”。当前代码在 JSON 解析后推进 maxSeqId，再用 maxSeqId+1 续传，并通过 checkpoint.seq_id-1 解决连续断线的 off-by-one；它能减少重复请求，但确认点早于 QAPair 提交，所以不能宣称 exactly-once。严格方案应把 receivedSeqId 与 committedSeqId 分开，只用最大连续已提交的 committedSeqId 续传，再通过 `(qid, seq_id)` 幂等键、内容摘要、乱序缓冲和缺口回补实现业务效果上的不重不漏。`AbortController` 也只会停止后续网络读取，不会撤回已经进入 Store 和渲染队列的任务；旧回调需通过稳定 QAPair id 隔离，队列消费和每个 `await` 后还要校验 request epoch。

---
