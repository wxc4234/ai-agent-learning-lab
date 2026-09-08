# 合并区间（LeetCode 56）

> 主题：数组 / 排序 / 区间 | 难度：Medium | 频率：高

## 考点（面试官在考察什么）

- 排序后维护当前合并区间，判断相交条件和边界是否闭合。

## 核心答案（能直接讲出口的版本）

按起点升序排序。遍历时，如果下一个区间起点不超过当前区间终点，就扩展终点；否则把当前区间结算，并开始新区间。

```ts
function merge(intervals: number[][]): number[][] {
    if (intervals.length <= 1) {
        return intervals.map(interval => [...interval]);
    }

    const sorted = intervals
        .map(interval => [...interval])
        .sort((a, b) => a[0] - b[0]);
    const result: number[][] = [sorted[0]];

    for (let i = 1; i < sorted.length; i++) {
        const current = result[result.length - 1];
        const next = sorted[i];

        if (next[0] <= current[1]) {
            current[1] = Math.max(current[1], next[1]);
        }
        else {
            result.push(next);
        }
    }

    return result;
}
```

## 复杂度分析

- 时间复杂度：O(n log n)，主要来自排序。
- 空间复杂度：O(n)，用于复制输入和输出。

## 易错点 / 相关题

- 若题目把区间定义成开区间或端点不相交，`<=` 需要改成 `<`。
- 相关题：插入区间、会议室 I/II、员工空闲时间。
