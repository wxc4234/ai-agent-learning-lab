# Vibe Coding 原题：100 个任务、最多 5 个并发

> 题型：异步调度 / 并发控制 | 难度：Medium | 频率：高

## 原题（公开面经转述）

用 JavaScript 设计一个线程并发池：有 100 个任务，最多同时执行 5 个；要求说明思路，最好保持结果顺序，并讨论某个任务失败时的行为。

JavaScript 没有共享内存意义上的“线程池”这个说法，面试中应先澄清为“Promise 任务并发池”。如果面试官强调 Web Worker，再把任务执行器换成 Worker 适配层，调度状态仍可复用。

## 参考实现

仓库已有完整题解：[限制并发数、保持结果顺序的异步任务调度器](../concurrency/async-task-pool.md)。现场作答时优先写出下面四个不变量，再逐步补代码：

- `running <= limit` 始终成立；
- 每个任务只领取一次；
- 结果按原始下标写回，而不是按完成顺序 `push`；
- 失败策略必须明确：快速失败、收集全部错误，或允许部分成功。

## 最小验收用例

```ts
const delays = [80, 10, 30, 5, 20];
let running = 0;
let maxRunning = 0;

const tasks = delays.map((delay, index) => async () => {
    running++;
    maxRunning = Math.max(maxRunning, running);
    await new Promise(resolve => setTimeout(resolve, delay));
    running--;
    return index;
});

// 期望：结果为 [0, 1, 2, 3, 4]，maxRunning 不超过 2（示例将上限设为 2）。
```

## 追问清单

1. 如何支持取消？给任务传入 `AbortSignal`，并区分“未开始”和“已在途”。
2. 如何动态调节并发？增加令牌桶/队列策略，并给出上限和恢复条件。
3. 如何避免一个慢任务阻塞后续任务？调度器只限制在途数，不按任务顺序等待启动。

## 来源

- [牛客：算法工程师精选面经合集](https://www.nowcoder.com/experience/645)（聚合页中的“抖音- AI 全栈工程师实习 一面二面凉经”，2026-03-19 条目；检索于 2026-09-08）
