# 用 TypeScript 实现限制并发数、保持结果顺序的异步任务调度器

> 主题：异步并发 | 频率：高

## 考点（面试官在考察什么）

- 并发上限、结果顺序、失败语义和测试。

## 核心答案（能直接讲出口的版本）

## 核心思路

启动最多 `limit` 个 Worker，共享 `nextIndex` 领取任务；结果按原下标写入数组，因此完成顺序不会影响返回顺序。任务失败时设置 `stopped` 并保存首个错误，其他 Worker 完成在途任务后不再领取新任务。

领取过程中的“检查停止状态、递增下标、调用任务”之间不能出现 `await`。JavaScript 单线程下，这段同步过程不会被其他 Worker 插入；但失败发生前已经启动的任务无法自动取消。

```ts
export async function runWithConcurrency<T>(
    tasks: ReadonlyArray<() => Promise<T>>,
    limit: number
): Promise<T[]> {
    if (!Number.isInteger(limit) || limit <= 0) {
        throw new RangeError('limit 必须是大于 0 的整数');
    }

    const taskList = [...tasks];
    const results = new Array<T>(taskList.length);
    let nextIndex = 0;
    let stopped = false;
    let firstError: unknown;

    function claimNext(): number | undefined {
        if (stopped || nextIndex >= taskList.length) {
            return undefined;
        }
        return nextIndex++;
    }

    async function worker(): Promise<void> {
        while (true) {
            const index = claimNext();
            if (index === undefined) {
                return;
            }

            try {
                results[index] = await taskList[index]();
            }
            catch (error) {
                if (!stopped) {
                    stopped = true;
                    firstError = error;
                }
                throw firstError;
            }
        }
    }

    const workerCount = Math.min(limit, taskList.length);
    await Promise.all(
        Array.from({length: workerCount}, () => worker())
    );
    return results;
}
```

## 边界与语义

- `limit` 必须是正整数；空任务数组返回空结果。
- 任务同步抛错和 Promise reject 使用同一失败语义。
- 首个错误会让返回值立即 reject；已启动任务仍会在后台自然结束，但不会再领取新任务。
- 如果要求取消在途任务，需要额外向任务传入 `AbortSignal`。

## 为什么领取任务不需要加锁

Worker 虽然是异步函数，但仍运行在同一个 JavaScript 线程中。边界检查、读取下标和 `nextIndex++` 之间没有 `await`，这段同步代码会一次执行完，其他 Worker 无法在中间插入，因此不会重复领取。

如果在检查和递增之间插入 `await`，多个 Worker 可能基于同一个旧状态通过检查；恢复后可能领取越界下标。若暂停前已经保存下标，还可能重复执行同一个任务。暂停期间也可能已有其他 Worker 将 `stopped` 设为 `true`，所以检查和领取必须保持为同步临界区。

## 并发与顺序验证

```ts
const sleep = (ms: number) =>
    new Promise<void>(resolve => setTimeout(resolve, ms));

async function testRunWithConcurrency() {
    const limit = 2;
    const delays = [100, 10, 20, 5, 15];
    let running = 0;
    let maxRunning = 0;
    const completionOrder: number[] = [];

    const tasks = delays.map((delay, index) => async () => {
        running++;
        maxRunning = Math.max(maxRunning, running);
        try {
            await sleep(delay);
            completionOrder.push(index);
            return index;
        }
        finally {
            running--;
        }
    });

    const results = await runWithConcurrency(tasks, limit);
    const expected = [0, 1, 2, 3, 4];

    if (JSON.stringify(results) !== JSON.stringify(expected)) {
        throw new Error('结果顺序不正确');
    }
    if (maxRunning > limit) {
        throw new Error(`最大并发数 ${maxRunning} 超过 ${limit}`);
    }

    console.log({completionOrder, results, maxRunning});
}
```
