# 无重复字符的最长子串（LeetCode 3）

> 主题：滑动窗口 / 哈希表 | 难度：Medium | 频率：高

## 考点（面试官在考察什么）

- 窗口何时扩张、何时收缩，以及如何维护窗口内的唯一性。

## 核心答案（能直接讲出口的版本）

右指针逐个加入字符；如果字符上次出现的位置仍在当前窗口内，就把左边界跳到它的下一位。每次更新窗口长度即可，不需要反复删除字符。

```ts
function lengthOfLongestSubstring(s: string): number {
    const lastIndex = new Map<string, number>();
    let left = 0;
    let best = 0;

    for (let right = 0; right < s.length; right++) {
        const previous = lastIndex.get(s[right]);
        if (previous !== undefined) {
            left = Math.max(left, previous + 1);
        }
        lastIndex.set(s[right], right);
        best = Math.max(best, right - left + 1);
    }

    return best;
}
```

## 复杂度分析

- 时间复杂度：O(n)，每个字符至多被处理常数次。
- 空间复杂度：O(min(n, 字符集大小))。

## 易错点 / 相关题

- 左指针只能向右移动，不能直接赋值为 `previous + 1`，否则会回退。
- 相关题：最小覆盖子串、长度最小的子数组、至多包含 K 个不同字符。
