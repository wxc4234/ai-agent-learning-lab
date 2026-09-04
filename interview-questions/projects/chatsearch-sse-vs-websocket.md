# ChatSearch：SSE 选型与流解析

## 1. 先判断业务通信模型

这个项目的核心通信过程是：用户提交一次问题，服务端在较长时间内持续返回 Token、引用、思考过程和结构化卡片。

```text
客户端：提交一次问题 ────────────────────────────────┐
                                                      ↓
服务端：waiting → thinking → markdown → card → endTurn
```

主要数据方向是服务端到客户端。客户端虽然也有停止生成、重新生成和发送下一轮问题，但这些操作频率低，而且可以通过新的 HTTP 请求或 `AbortController` 完成。因此它不是一个需要双方高频、持续互相发消息的全双工场景。

## 2. SSE 与 WebSocket 的取舍

| 对比项 | SSE | WebSocket | 当前项目的判断 |
|---|---|---|---|
| 通信方向 | 服务端单向流式返回 | 持续双向通信 | 回答生成以单向返回为主 |
| 业务边界 | 一个 HTTP 请求对应一轮回答 | 多轮消息复用一条连接 | 一轮一请求更容易隔离状态 |
| 鉴权与网关 | 复用 Cookie、Header、HTTP 网关 | 需要处理 Upgrade 和长连接治理 | SSE 改造成本更低 |
| 中断 | `AbortController` 终止当前请求 | 发送控制消息后再关闭或保留连接 | 当前停止生成不需要全双工通道 |
| 恢复 | 需要序号与 checkpoint | 同样需要自定义 ACK、序号和补偿 | WebSocket 不会自动解决数据一致性 |
| 适合场景 | AI 回答、日志流、通知流 | IM、语音、协同编辑、游戏 | 当前业务更符合 SSE |

## 3. 为什么没有直接使用原生 EventSource

原生 `EventSource` 主要面向 GET，请求头和请求体的控制能力有限。AI 对话需要提交模型参数、上下文、实验信息和文件信息，因此项目封装了基于 `fetch` 的 SSE：

- 使用 POST 提交完整 JSON Body；
- 支持自定义 Header 和 Cookie；
- 校验响应是否为 `text/event-stream`；
- 使用流式 Reader 逐包解析 SSE；
- 使用 `AbortController` 支持停止生成；
- 自定义 `onopen`、`onmessage`、`onclose`、`onerror`；
- 支持服务端 `reconnect` 事件和业务级断点续传。

所以这里选择的不是“功能受限的原生 EventSource”，而是“基于 HTTP Fetch 的可控流式协议”。

## 4. `fetch()` 拿到 `response.body` 后，怎样得到完整 SSE event

### 4.1 先区分四种边界

```text
TCP/HTTP chunk
    → SSE 行（data/event/id/retry）
    → SSE event（空行收口）
    → 业务消息（JSON.parse）
```

`reader.read()` 一次只能保证返回一段按顺序到达的字节，不保证它正好对应服务端的一次 `write()`，更不保证它是一条完整 JSON。因此项目不会对每个 chunk 直接 `JSON.parse`。

### 4.2 读取层拿到的是 `Uint8Array`

```typescript
const response = await fetch('/chat', {
    method: 'POST',
    headers: {
        Accept: 'text/event-stream',
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(requestBody),
    signal: controller.signal,
});

if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
}

if (!response.headers.get('content-type')?.includes('text/event-stream')) {
    throw new Error('unexpected content-type');
}

if (!response.body) {
    throw new Error('ReadableStream is not supported');
}

const reader = response.body.getReader();

while (true) {
    const {done, value} = await reader.read();
    if (done) {
        break;
    }
    // value 是 Uint8Array，先交给字节级行解析器
    lineParser.push(value);
}
```

### 4.3 先缓存原始字节，再按完整行解码

项目的 `getLines()` 会跨 chunk 保留未完成的字节，识别 `\n`、`\r` 和 `\r\n`，只在确认得到完整行以后才交给 `TextDecoder`。接近代码级的逻辑如下：

```typescript
function createLineParser(onLine: (line: Uint8Array) => void) {
    let buffer = new Uint8Array(0);

    return {
        push(chunk: Uint8Array) {
            buffer = concatBytes(buffer, chunk);
            let lineStart = 0;

            for (let i = 0; i < buffer.length; i++) {
                const byte = buffer[i];
                if (byte !== 0x0a && byte !== 0x0d) {
                    continue;
                }

                // chunk 最后恰好是 \r，先等下一包，判断是否组成 \r\n
                if (byte === 0x0d && i === buffer.length - 1) {
                    break;
                }

                onLine(buffer.slice(lineStart, i));

                if (byte === 0x0d && buffer[i + 1] === 0x0a) {
                    i++;
                }
                lineStart = i + 1;
            }

            // 未形成完整行的字节留给下一个 chunk
            buffer = buffer.slice(lineStart);
        },
    };
}
```

这个顺序同时解决了 UTF-8 字符跨 chunk 截断问题。例如一个中文字占 3 个字节，前 2 个在 chunk1、后 1 个在 chunk2，解析器会先合并原始字节，不会在字符中间调用非流式 `decode()`。

> 另一种可行实现是对每个 chunk 调用 `decoder.decode(chunk, {stream: true})`，让 `TextDecoder` 保留未完成字符。当前项目走的是“原始字节缓存到完整行，然后解码”这条路径。

### 4.4 SSE 字段解析：空行才是 event 的完成点

```typescript
const decoder = new TextDecoder();
let message = createEmptyMessage();

function onLine(lineBytes: Uint8Array) {
    if (lineBytes.length === 0) {
        // 空行收口：到这里才算得到完整 SSE event
        if (message.dataLines.length) {
            emit({
                event: message.event || 'message',
                data: message.dataLines.join('\n'),
                id: message.id,
                retry: message.retry,
            });
        }
        message = createEmptyMessage();
        return;
    }

    const line = decoder.decode(lineBytes);
    if (line.startsWith(':')) {
        return; // comment / heartbeat
    }

    const colon = line.indexOf(':');
    const field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) {
        value = value.slice(1);
    }

    if (field === 'data') {
        message.dataLines.push(value);
    }
    else if (field === 'event') {
        message.event = value;
    }
    else if (field === 'id') {
        message.id = value;
    }
    else if (field === 'retry' && /^\d+$/.test(value)) {
        message.retry = Number(value);
    }
}
```

只有 `emit()` 以后才进入：

```typescript
const envelope = JSON.parse(event.data);
```

连接异常结束时，如果最后一条还没有空行收口，就不把它当成已确认的业务包，而是通过 checkpoint 重新获取。

### 4.5 题目中的三个 chunk 如何演进

```text
chunk1: data: {"seq_id":100,"content":"hel
```

没有换行，整段留在字节 buffer 中，不做 JSON 解析。

```text
chunk2: lo"}\n\ndata: {"seq_id":101,"content":"wor
```

与 chunk1 合并后，`seq_id=100` 的 `data` 行和空行都已完整，因此发出 event 100；`seq_id=101` 仍然是未完成行，继续缓存。

```text
chunk3: ld"}\n\n
```

与剩余字节合并后才发出 event 101。所以正确结论是：

```text
TCP chunk 不是 SSE event
SSE data 行也不一定是完整 event
只有空行收口后，才能对累积 data 执行 JSON.parse
```

## 5. 为什么 WebSocket 在这里反而会增加复杂度

如果使用 WebSocket，多轮回答共用一条连接，前端还需要额外维护：

1. 每条消息属于哪个 QAPair；
2. 多轮对话并发时如何路由；
3. 心跳与连接保活；
4. 页面隐藏、网络切换后的重连；
5. 重连后哪些消息已经收到、哪些需要补发；
6. 网关发布或服务端扩缩容时如何迁移连接；
7. 多个 Tab 或多个会话是否共享连接。

这些问题不是 WebSocket 不能解决，而是解决成本更高，并且最后仍然要实现 `qid + seq_id + checkpoint + 业务幂等`，才可能实现业务效果上的不重不漏。

## 6. SSE 的不足以及项目如何补齐

| SSE 的不足 | 项目中的处理 |
|---|---|
| 断线重连可能重复或缺失数据 | 当前用 `seq_id` 和 `checkpoint` 降低重放范围；严格语义还需要业务提交点、去重和缺口检测 |
| 旧请求回调可能晚到 | 所有回调绑定稳定的 QAPair id |
| 无法知道 DOM 是否渲染完成 | 增加渲染队列和完成确认机制 |
| 服务端返回速度快于页面消费 | 在应用层实现背压，不直接无节制更新 DOM |
| 用户需要停止回答 | `AbortController` 中断，并区分用户停止、网络错误和新请求打断 |

## 7. 面试回答口径

> 我选择 SSE，不是因为 WebSocket 做不了，而是因为项目的核心通信模型是“一次提问、服务端单向持续返回答案”。SSE 可以复用现有 HTTP 的鉴权、网关和监控，一轮请求也天然对应一个 QAPair，状态隔离更清晰。我们又基于 fetch 封装了 SSE，因此支持 POST、Header、Body 和主动中断。WebSocket 更适合高频双向通信，在这里使用反而要额外维护心跳、消息路由和连接恢复，而断点续传、顺序和幂等问题仍然需要业务层解决。

> 如果面试官继续追问解析层，我会补充：`response.body` 读到的是无业务边界的 `Uint8Array` chunk。当前项目先跨 chunk 缓存原始字节，识别 CR/LF 拆出完整 SSE 行，再解码 `data/event/id/retry`；遇到空行才收口一条 event，最后才 `JSON.parse`。这样同时解决了 TCP chunk 与 event 边界不一致，以及 UTF-8 字符跨 chunk 的问题。checkpoint 的严格幂等语义见“深挖 B”，`abort()` 后的队列失效问题见“专题三”。

---
