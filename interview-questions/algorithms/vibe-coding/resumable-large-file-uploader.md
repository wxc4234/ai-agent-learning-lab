# Vibe Coding 原题：弱网下上传 1G 文件

> 题型：浏览器工程 / 分片上传 / 可靠性 | 难度：Medium | 频率：高（AI 全栈实战题）

## 原题（公开面经转述）

用户在弱网环境下想上传一个 1G 的文件，如何设计这个上传器？请讲清楚分片、并发、失败重试、断点续传、完整性校验和服务端合并。

这不是单纯的“手写一个 `fetch`”，面试官通常会要求先画状态机，再写一个能运行的分片调度核心。涉及鉴权、对象存储或服务端接口时，应先和面试官约定接口，不要擅自假设云厂商 SDK。

## 设计答案（能直接讲出口的版本）

1. 客户端按固定大小切片，例如 8 MiB；先用文件大小、修改时间和首尾摘要生成 `fileFingerprint`。
2. 调用 `init` 获取 `uploadId` 和已完成分片列表，跳过服务端已有分片。
3. 用并发池上传分片；每个分片使用指数退避重试，并通过 `AbortController` 支持取消。
4. 每片成功后持久化进度（IndexedDB 优先，`localStorage` 只存小元数据），刷新页面后重新 `resume`。
5. 所有分片完成后调用 `complete`，服务端按 `partNumber` 排序合并，并校验总大小/哈希。

## 分片计划核心（TypeScript）

```ts
type Chunk = {
    partNumber: number;
    start: number;
    end: number;
};

function createChunkPlan(fileSize: number, chunkSize = 8 * 1024 * 1024): Chunk[] {
    if (!Number.isInteger(fileSize) || fileSize < 0 || chunkSize <= 0) {
        throw new RangeError('invalid fileSize or chunkSize');
    }

    const chunks: Chunk[] = [];
    let partNumber = 1;
    for (let start = 0; start < fileSize; start += chunkSize) {
        chunks.push({
            partNumber,
            start,
            end: Math.min(fileSize, start + chunkSize)
        });
        partNumber++;
    }
    return chunks;
}

async function uploadWithRetry(
    send: () => Promise<void>,
    maxRetries = 3,
    baseDelayMs = 300
): Promise<void> {
    for (let attempt = 0; ; attempt++) {
        try {
            await send();
            return;
        }
        catch (error) {
            if (attempt >= maxRetries) {
                throw error;
            }
            const delay = baseDelayMs * 2 ** attempt;
            await new Promise(resolve => setTimeout(resolve, delay));
        }
    }
}
```

## 必须主动说明的边界

- 不能只依赖客户端进度；服务端 `init/list-parts` 才是断点续传的事实来源。
- 重试必须带幂等键（例如 `uploadId + partNumber`），否则可能重复计费或生成脏分片。
- 服务端合并后要异步清理过期分片，并限制单个上传会话的总时长和分片数。
- 传输加密、权限校验、文件类型白名单和病毒扫描属于安全闭环，不能因为题目偏前端就省略。

## 追问清单

1. 并发数动态调节依据是什么？（失败率、RTT、浏览器连接数、设备内存）
2. 标签页关闭或网络切换后如何恢复？
3. 如何避免同一文件被重复上传？
4. 服务端合并超时，客户端应该如何查询最终状态？

## 来源

- [牛客：算法工程师精选面经合集](https://www.nowcoder.com/experience/645)（聚合页中的“抖音- AI 全栈工程师实习 一面二面凉经”，2026-03-19 条目；检索于 2026-09-08）
