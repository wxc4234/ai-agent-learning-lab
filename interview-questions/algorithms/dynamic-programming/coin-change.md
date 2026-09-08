# 零钱兑换（LeetCode 322）

> 主题：动态规划 / 完全背包 | 难度：Medium | 频率：高

## 考点（面试官在考察什么）

- 状态定义、无解值初始化，以及为什么每个硬币可以被重复使用。

## 核心答案（能直接讲出口的版本）

`dp[amount]` 表示凑出金额 `amount` 所需的最少硬币数。枚举金额时，尝试最后放入的每一种硬币：`dp[x] = min(dp[x - coin] + 1)`。

```ts
function coinChange(coins: number[], amount: number): number {
    const unreachable = amount + 1;
    const dp = Array(amount + 1).fill(unreachable);
    dp[0] = 0;

    for (let current = 1; current <= amount; current++) {
        for (const coin of coins) {
            if (coin <= current) {
                dp[current] = Math.min(
                    dp[current],
                    dp[current - coin] + 1
                );
            }
        }
    }

    return dp[amount] === unreachable ? -1 : dp[amount];
}
```

## 复杂度分析

- 时间复杂度：O(amount × coins.length)。
- 空间复杂度：O(amount)。

## 易错点 / 相关题

- `dp[0]` 必须是 0；无解不能初始化为 `Infinity` 后再做不必要的特殊判断，也可以使用 `amount + 1` 作为哨兵。
- 若题目改成“每种硬币最多一次”，状态转移和循环方向要改成 0/1 背包。
