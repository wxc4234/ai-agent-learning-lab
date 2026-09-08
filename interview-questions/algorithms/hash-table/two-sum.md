# 两数之和（LeetCode 1）

> 主题：哈希表 | 难度：Easy | 频率：高

## 考点（面试官在考察什么）

- 能否用空间换时间，把“查找互补值”从 O(n) 降到 O(1) 平均复杂度。

## 核心答案（能直接讲出口的版本）

遍历数组时，当前值为 `x`，只需要查找之前是否见过 `target - x`。用 `Map` 保存“值 → 下标”，先查后存，避免同一个元素被重复使用。

```ts
function twoSum(nums: number[], target: number): [number, number] | [] {
    const indexByValue = new Map<number, number>();

    for (let i = 0; i < nums.length; i++) {
        const complement = target - nums[i];
        const previousIndex = indexByValue.get(complement);
        if (previousIndex !== undefined) {
            return [previousIndex, i];
        }
        indexByValue.set(nums[i], i);
    }

    return [];
}
```

## 复杂度分析

- 时间复杂度：O(n) 平均。
- 空间复杂度：O(n)。

## 易错点 / 追问

- `Map#get` 返回 `undefined`，下标 0 不能用真假判断代替存在性判断。
- 如果要求所有解，需要继续遍历并定义重复值的去重规则。
- 追问：数组有序时可以用双指针，空间降为 O(1)，但下标映射需要额外处理。
