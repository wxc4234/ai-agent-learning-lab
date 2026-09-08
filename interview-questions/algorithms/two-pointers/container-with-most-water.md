# 盛最多水的容器（LeetCode 11）

> 主题：双指针 | 难度：Medium | 频率：高

## 考点（面试官在考察什么）

- 能否证明双指针移动较短边，而不是靠暴力枚举所有区间。

## 核心答案（能直接讲出口的版本）

面积由较短边决定。固定两端时，移动较长边不会让短边变高，宽度却一定减少，因此只有移动较短边才可能得到更优解。

```ts
function maxArea(height: number[]): number {
    let left = 0;
    let right = height.length - 1;
    let best = 0;

    while (left < right) {
        const width = right - left;
        const current = Math.min(height[left], height[right]) * width;
        best = Math.max(best, current);

        if (height[left] <= height[right]) {
            left++;
        }
        else {
            right--;
        }
    }

    return best;
}
```

## 复杂度分析

- 时间复杂度：O(n)。
- 空间复杂度：O(1)。

## 易错点 / 相关题

- 先算当前面积再移动指针。
- 相关题：三数之和、接雨水（需要换成单调栈或双指针证明）。
