# 前 K 个高频元素（LeetCode 347）

> 主题：哈希表 / 堆 | 难度：Medium | 频率：高

## 考点（面试官在考察什么）

- 频次统计、固定大小小顶堆，以及“为什么堆大小只保留 K 个”的解释能力。

## 核心答案（能直接讲出口的版本）

先统计每个数字的频次，再维护一个按频次排序、大小不超过 `k` 的小顶堆。堆顶是当前保留集合中频次最低的元素，遇到更高频元素就替换它。

```ts
type Pair = [value: number, count: number];

function topKFrequent(nums: number[], k: number): number[] {
    const counts = new Map<number, number>();
    for (const num of nums) {
        counts.set(num, (counts.get(num) ?? 0) + 1);
    }

    const entries = [...counts.entries()]
        .map(([value, count]) => [value, count] as Pair)
        .sort((a, b) => b[1] - a[1]);

    return entries.slice(0, k).map(([value]) => value);
}
```

> 上面代码用排序保持实现短小，现场追问“要求 O(n log k)”时，再将 `entries` 替换为自实现小顶堆；频率题的关键是先讲清数据流和复杂度取舍。

## 复杂度分析

- 当前实现：时间 O(n + m log m)，空间 O(m)，`m` 为不同元素个数。
- 小顶堆版本：时间 O(n + m log k)，空间 O(m + k)。

## 易错点 / 相关题

- 面试官常要求“至少两种解法”：排序、堆、桶排序/快速选择。
- 相关题：数组中的第 K 个最大元素、合并 K 个有序链表、数据流中的中位数。
