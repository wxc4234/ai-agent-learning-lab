# ChatSearch：Checkpoint 语义与一致性

> **阅读边界：**第 1～5 节描述【当前实现】及其缺口；第 6～9 节是【改进建议】；第 10 节区分当前进程内恢复与跨进程恢复。面试时不能把改进后的幂等账本和原子事务说成已经上线。

<details>
<summary><strong>展开查看：checkpoint 确认点、去重、乱序、exactly-once 与跨进程恢复</strong></summary>

## 1. 先纠正结论：当前实现不能宣称“不重不漏”

这个问题必须先把结论说清楚：

> 当前 `maxSeqId + 1` 是一种 best-effort 的续传游标。它解决了常见重连和连续重连的序号计算问题，但确认点早于 QAPair 业务提交，而且没有完整的重复包、乱序包和冲突包处理。因此它不是真正的 exactly-once，也还不能严格称为“至少一次传输 + 客户端幂等”。

此前如果把这个方案直接表述为“保证不重不漏”，是不严谨的。更准确的说法是：

- QAPair id 绑定解决的是旧回调串写；
- `checkpoint.seq_id = maxSeqId + 1` 解决的是常见续传位置和 off-by-one；
- 它们不能单独证明每个业务包恰好处理一次；
- 要实现业务效果上的不重不漏，还必须增加业务提交点、连续序号水位、幂等账本、内容冲突检测和缺口回补。

## 2. 当前代码把确认点放在哪里

一条消息的完整路径可以拆成：

```text
网络字节到达
  → SSE framing 解析出完整 event
  → JSON.parse
  → handleMessageData
  → trackSeqId(seq_id)             ← 当前 maxSeqId 在这里推进
  → waiting/status 等业务判断
  → triggerEvent(message/complete)
  → ChatStream Store / QAPair 更新
  → 渲染队列
  → DOM 完成
```

对应代码位于 `packages/chat-sse/src/chat-sse.ts`。核心逻辑可以简化为：

```typescript
onmessage: event => {
    if (!event.data) {
        return;
    }

    const data = JSON.parse(event.data);
    this.onMessage(data, event.event);
}

onMessage(data, type): void {
    // handleMessageData 是 async，但这里没有 await
    this.handleMessageData(data, type);
}

async handleMessageData(data, type): Promise<void> {
    const {seq_id, status} = data;

    // JSON 已解析，但还没有确认 QAPair 写入成功
    this.trackSeqId(seq_id);

    if (this.isWaitingForCallback) {
        return;
    }

    await this.waitSendBackDataIfNeeded(data);

    if (!MSG_CONTINUE_STATUS.includes(status)) {
        return;
    }

    this.triggerEvent('message', data);
}

private trackSeqId(seqId: number): void {
    if (seqId > this.maxSeqId) {
        this.maxSeqId = seqId;
    }
}
```

所以这里“收到一包”的实际定义是：

> SSE 层已经拿到一个完整 event，且 `JSON.parse` 成功，`handleMessageData` 已经读到了它的 `seq_id`。

它不是“网络字节刚到”，也不是“已写入 QAPair”，更不是“DOM 已完成”。

## 3. 为什么当前会把确认点放得这么早

这种设计的出发点是把 checkpoint 当成传输层游标：SSE 层无需了解 QAPair、组件类型和 DOM，只要解析到包就可以推进位置；重连时也能快速减少服务端重放的数据量。

它隐含了三个假设：

1. 服务端一定按连续递增的 `seq_id` 发包；
2. JSON 解析成功后，后续业务回调和 Store 写入不会失败或跳过；
3. 同一个 `seq_id` 不会重复，也不会出现不同内容。

在这些假设都成立时，JSON 解析点看起来近似等于业务接收点。但是代码里 `trackSeqId` 后面仍然存在等待回调、状态过滤、事件分发和 QAPair 更新，因此二者并不等价。

## 4. `seq_id=10` 的丢包窗口确实存在

面试官给出的场景可以按时间线还原：

```text
T1  SSE 完成 JSON.parse，得到 seq_id=10
T2  trackSeqId(10)，maxSeqId 从 9 变成 10
T3  还没有 triggerEvent，或者 QAPair 写入尚未成功
T4  连接中断并触发 reconnect
T5  新请求携带 checkpoint.seq_id = maxSeqId + 1 = 11
T6  服务端从 11 开始返回，10 不再重放
```

如果第 10 包没有进入 QAPair，它就会永久缺失。以下情况都可能制造这个窗口：

- `trackSeqId` 后面的业务状态校验决定跳过该包；
- waiting 回调发生异步等待或拒绝；
- message 回调执行异常；
- Store/QAPair 更新失败；
- 异步 `handleMessageData` 没有被上层 await，失败没有转化为明确的 nack；
- 更高序号先推进了 `maxSeqId`，但中间序号尚未提交。

因此，`maxSeqId` 目前更准确的名字应是 `receivedHighWatermark` 或 `parsedHighWatermark`，不能把它当成 `committedSeqId`。

## 5. 当前重复包到底有没有去重

没有完整去重。`trackSeqId` 的条件只是：

```typescript
if (seqId > this.maxSeqId) {
    this.maxSeqId = seqId;
}
```

当 `seq_id <= maxSeqId` 时，代码只是不再更新最大值，当前包仍会继续经过业务回调。它不等价于：

```typescript
if (seqId <= maxSeqId) {
    return; // 当前代码没有这一步
}
```

而且即使简单增加这个 `return`，也仍然不正确。因为 `maxSeqId` 只是“见过的最大值”，不是“连续提交水位”。例如先收到 12，再收到 11，如果 `maxSeqId=12` 后直接丢弃 11，就会把本来可补齐的第 11 包误判成重复包。

当前行为可以归纳为：

| 输入情况 | 当前 `maxSeqId` 行为 | 当前业务处理 | 风险 |
|---|---|---|---|
| `seq=10`，当前最大为 9 | 更新为 10 | 继续处理 | 业务未提交时 checkpoint 已前移 |
| 再次收到相同 `seq=10` | 不更新 | 仍可能继续处理 | 内容可能重复追加 |
| 先收到 12，11 尚未到 | 直接更新为 12 | 处理 12 | 未发现 11 的缺口 |
| 后收到 11 | 不更新 | 仍处理 11 | 到达顺序可能污染业务顺序 |
| 相同 `seq=10`、内容不同 | 不更新 | 两份内容都可能处理 | 无法识别协议冲突 |
| JSON 解析失败 | 不更新 | 不处理 | 这一点是安全的，但不覆盖后续失败 |

项目中 UI 的 `chat-answer/index.ts` 还会在 `handlePacks` 中记录 `retryExtraData.seq_id`。这个时点比 SSE 的 `trackSeqId` 更晚，但仍然早于 DOM 完成，而且两处序号口径没有形成统一的 commit 协议。相关判断还使用 truthy 条件，`seq_id=0` 需要额外注意不能被当成“无值”。

## 6. 正确的确认点应该放在哪里

严格方案需要区分三个水位：

```typescript
interface StreamCursor {
    // 传输诊断：见过的最大序号，不能用于续传
    receivedHighWatermark: number;

    // 业务确认：从 0 开始连续、且已经成功提交的最大序号
    committedSeqId: number;

    // 展示确认：已经完成渲染的最大连续序号，只影响 UI 完成态
    renderedSeqId: number;
}
```

checkpoint 只能这样计算：

```typescript
checkpoint.seq_id = cursor.committedSeqId + 1;
```

确认点应放在：

> 包已经完成协议校验和分支路由，并通过 `(qid, seq_id)` 幂等键成功写入 QAPair/Store；如果渲染任务不能从 Store 重建，还要在同一个提交中记录 render intent。提交成功后，才推进 `committedSeqId`。

不应该等 DOM 完成，原因是：

- DOM 是投影，不是业务数据源；
- 组件可能因为切列、路由或虚拟列表暂时卸载；
- DOM 渲染慢，不应阻塞网络确认；
- 页面可以根据 QAPair/Store 再次渲染；
- answer-end 的 DOM 屏障和 checkpoint 的数据确认是两个不同问题。

如果当前渲染队列完全存在组件内存里、无法由 Store 重建，那么仅写 QAPair 也不够。此时需要把“已应用的业务数据”和“待渲染任务”以 outbox 方式一起提交，渲染器再消费 outbox。这样即使渲染器稍后失败，数据也不会因为 checkpoint 前移而消失。

## 7. 去重不能只判断 `seq_id <= maxSeqId`

正确的幂等键至少是：

```text
(conversationId 或 qid, seq_id)
```

只使用 `seq_id` 不够，因为不同回答可能都从 0 开始。除幂等键外，还要保存规范化内容的摘要：

```typescript
type AppliedRecord = {
    key: `${string}:${number}`; // qid:seq_id
    payloadHash: string;
    appliedAt: number;
};
```

内容摘要用于区分两类情况：

- 相同 key、相同 hash：真正的重放，直接忽略；
- 相同 key、不同 hash：协议冲突，不能静默覆盖或追加。

协议冲突说明服务端对同一业务序号给出了两个事实。客户端应该停止推进 checkpoint，上报 `SEQ_PAYLOAD_CONFLICT`，并从服务端权威快照重建该轮回答；不能选择“以后到的为准”，否则不同客户端可能得到不同结果。

## 8. 乱序、跳号和冲突包的判断顺序

需要维护的关键字段是：

```typescript
interface ResumeContext {
    qid: string;
    receivedHighWatermark: number;
    committedSeqId: number;              // 最大连续已提交序号
    pending: Map<number, Packet>;         // 乱序缓冲区
    appliedHash: Map<number, string>;     // 已提交幂等账本
    gapTimer?: number;
    terminalSeqId?: number;               // endTurn 所在序号
    consumeChain: Promise<void>;          // 单写者队列，防止 async 回调并发提交
}
```

判断顺序必须先验证身份和内容，再判断已提交、待提交和缺口：

```typescript
function onSsePacket(packet: Packet): void {
    // 当前 onMessage 没有 await async handler；改造后先串行化业务消费。
    ctx.consumeChain = ctx.consumeChain
        .then(() => ingest(packet))
        .catch(error => failStream(error));
}

async function ingest(packet: Packet): Promise<void> {
    validatePacketShape(packet);

    if (packet.qid !== ctx.qid) {
        throw new ProtocolError('QID_MISMATCH');
    }

    const seq = packet.seq_id;
    const hash = digest(canonicalize(packet));
    ctx.receivedHighWatermark = Math.max(ctx.receivedHighWatermark, seq);

    // 1. 已经提交过的序号
    if (seq <= ctx.committedSeqId) {
        const oldHash = await ledger.getHash(ctx.qid, seq);

        if (oldHash === hash) {
            return; // 相同内容重放，幂等丢弃
        }

        throw new ProtocolError('SEQ_PAYLOAD_CONFLICT');
    }

    // 2. 已经在乱序缓冲区中的序号
    const pendingPacket = ctx.pending.get(seq);
    if (pendingPacket) {
        if (digest(canonicalize(pendingPacket)) === hash) {
            return; // 相同乱序包重复到达
        }

        throw new ProtocolError('SEQ_PAYLOAD_CONFLICT');
    }

    // 3. 比预期序号大，说明出现缺口；缓存但不推进 committedSeqId
    const expected = ctx.committedSeqId + 1;
    if (seq > expected) {
        ctx.pending.set(seq, packet);
        ensureGapRecovery(expected);
        enforcePendingWindowLimit();
        return;
    }

    // 4. seq === expected，可以提交，并继续排空已到达的连续包
    ctx.pending.set(seq, packet);
    await drainContiguousPackets();
}
```

连续提交的关键是：只能推进到没有缺口的位置，不能做 `Math.max`：

```typescript
async function drainContiguousPackets(): Promise<void> {
    while (true) {
        const nextSeq = ctx.committedSeqId + 1;
        const packet = ctx.pending.get(nextSeq);

        if (!packet) {
            return;
        }

        const hash = digest(canonicalize(packet));
        const key = `${ctx.qid}:${nextSeq}`;

        // QAPair、幂等账本、checkpoint 和 render outbox
        // 应处于同一个事务或同一个不可分割的业务提交中。
        await businessStore.transaction(async tx => {
            const existing = await tx.appliedLedger.get(key);

            if (existing) {
                if (existing.payloadHash !== hash) {
                    throw new ProtocolError('SEQ_PAYLOAD_CONFLICT');
                }
                return;
            }

            await tx.qapair.applyPacket(ctx.qid, packet);
            await tx.renderOutbox.put(key, toRenderTask(packet));
            await tx.appliedLedger.put(key, {payloadHash: hash});
            await tx.resumeCursor.put(ctx.qid, {committedSeqId: nextSeq});
        });

        ctx.pending.delete(nextSeq);
        ctx.appliedHash.set(nextSeq, hash);
        ctx.committedSeqId = nextSeq;

        if (packet.endTurn) {
            ctx.terminalSeqId = nextSeq;
        }
    }
}
```

不同异常的处理策略如下：

| 场景 | 判断 | 处理 |
|---|---|---|
| 重复包 | `seq <= committed` 且 hash 相同 | 丢弃，不重复写 QAPair、不重复入渲染队列 |
| 旧乱序包 | 序号较小且已提交 | 按 hash 判断重复或冲突 |
| 跳号 | `seq > committed + 1` | 缓冲高序号，从 `committed + 1` 请求缺口，不推进 checkpoint |
| 乱序后补齐 | 收到 `committed + 1` | 顺序提交，并连续排空 pending |
| 同序号不同内容 | key 相同、hash 不同 | 停止该流，上报冲突，从权威快照重建 |
| endTurn 提前到达 | endTurn 序号前仍有缺口 | 只缓冲，不允许整轮完成 |
| pending 持续增长 | 超出窗口或 gap 超时 | 中断当前连接，从 committed checkpoint 续传或全量重建 |

`pending` 必须有最大窗口和超时时间，否则异常服务端持续发送高序号会造成内存增长。

## 9. 这到底是不是 exactly-once

要区分三层语义：

| 层次 | 能否做到 | 说明 |
|---|---|---|
| 网络传输 exactly-once | 通常不能直接承诺 | 断线时客户端和服务端无法仅凭连接状态判断最后一包是否成功处理 |
| 当前项目实现 | 不是 | checkpoint 在 JSON 解析后前移，又没有统一幂等账本和缺口检测 |
| 改造后的业务效果 | 可以做到近似 exactly-once effect | 服务端至少一次重放 + 客户端原子提交和幂等消费 |

所以正式表述应该是：

> 我们不宣称底层 SSE 具备 exactly-once 传输。可靠方案是服务端按 checkpoint 提供至少一次重放，客户端用 `(qid, seq_id)` 做幂等键，把业务数据、幂等记录和提交游标原子写入，从而实现每个包对 QAPair 只生效一次的 exactly-once business effect。

这里“原子”非常关键。如果先写 QAPair、后写幂等账本，中间崩溃会导致重放再次追加；如果先写 checkpoint、后写 QAPair，又会回到第 10 包永久丢失的问题。

对当前代码则应该直接承认：它是带早确认的 best-effort resume。在异常窗口里既可能漏，也可能重复，不能包装成严格的至少一次或恰好一次。

## 10. 页面刷新或进程被杀后是否还能续传

当前 `ChatSSE.maxSeqId` 是 SSE 实例中的内存字段，组件里的 `retryExtraData.seq_id` 也是页面内存状态。因此：

```text
同一个页面进程内网络重连：maxSeqId 仍在，续传能力成立
页面刷新 / WebView 被回收 / 浏览器进程被杀：内存游标消失，原续传能力不成立
```

项目可以通过后端历史记录、任务消息中心或从 `seq_id=0` 重新拉取来重建回答，但那属于“从服务端权威数据恢复”，不是内存 `maxSeqId + 1` 跨进程继续工作。

要支持真正的跨刷新恢复，有两种方案：

### 方案 A：服务端维护权威进度

服务端保存回答快照、事件日志和 resume token。页面重新打开后：

1. 用会话 id/qid 查询服务端快照；
2. 用快照恢复 QAPair；
3. 服务端返回权威的下一序号或 resume token；
4. 客户端从该位置继续订阅；
5. 客户端仍保留幂等处理，防止快照边界重放。

这是更可靠的方案，因为生成任务本来就在服务端执行。

### 方案 B：客户端持久化提交状态

使用 IndexedDB 持久化：

```typescript
type PersistedResumeState = {
    qid: string;
    committedSeqId: number;
    qapairSnapshotOrEventLog: unknown;
    appliedHashes: Record<number, string>;
    protocolVersion: number;
    updatedAt: number;
};
```

不能只把 `committedSeqId` 写进 localStorage。若游标持久化成功、QAPair 数据尚未持久化，进程此时被杀，重启后仍会跳过缺失数据。必须让 QAPair 数据、幂等账本和 committed cursor 处在同一个 IndexedDB transaction 中，或者使用 write-ahead log。

客户端恢复时还要校验：

- qid 是否仍然有效；
- 服务端事件保留期是否过期；
- 协议版本和模型配置是否匹配；
- 持久化记录是否完整；
- 服务端是否已经形成最终权威答案。

如果任一条件不满足，应丢弃本地 resume cursor，转为拉取服务端全量快照，不能拿一个陈旧的 `maxSeqId` 盲目续传。

## 11. 面试现场的完整回答

> 你指出的窗口是存在的。当前代码不是在 DOM 完成后确认，也不是在 QAPair 写入后确认，而是在 SSE event 完成 JSON 解析后就调用 trackSeqId。trackSeqId 只做 `seqId > maxSeqId` 时更新最大值；对于 `seqId <= maxSeqId` 并不会 return，所以它不是去重器。这样第 10 包可能已经把 maxSeqId 推到 10，但在状态过滤、异步回调或 Store 写入前失败，重连从 11 开始就会漏掉第 10 包。先到 12 也会直接把 max 推到 12，当前没有缺口检测；后到 11 仍可能被处理；相同序号不同内容也没有 hash 冲突检测。
>
> 所以我会纠正“不重不漏”的说法：当前只是 best-effort resume，不是真正的 exactly-once，也还不是完整的至少一次加客户端幂等。严格方案要把 receivedHighWatermark、committedSeqId 和 renderedSeqId 分开。received 只做监控，checkpoint 只能取最大连续已提交的 committedSeqId+1。确认点放在包通过校验、按 `(qid, seq_id)` 幂等写入 QAPair/Store，并且记录可恢复的渲染任务之后；不等 DOM，因为 DOM 是可以从 Store 重建的展示投影。
>
> 对重复包，我会查询 `(qid, seq_id)` 的幂等账本：hash 相同直接丢弃，hash 不同视为协议冲突并从权威快照重建。对于跳号，只把高序号放进 pending，不推进 committedSeqId，从 `committed+1` 请求缺口；缺口补齐后再顺序排空。业务数据、hash 账本和 committed cursor 必须原子提交。这样底层仍然是至少一次传输，但客户端可以做到 exactly-once business effect。
>
> 最后，当前 maxSeqId 只在内存中，页面刷新或进程被杀后原续传能力就不成立。跨进程恢复必须依赖服务端事件日志/快照和 resume token，或者把 QAPair、幂等账本与 committed cursor 原子持久化到 IndexedDB。只持久化一个 maxSeqId 仍然会产生同样的丢包窗口。

</details>

---
