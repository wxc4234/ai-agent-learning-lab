# 力扣算法

编码能力是社招的第一道硬门槛，AI 全栈岗通常考中等难度（Medium）为主，重点在**思路表达 + 复杂度分析**，不要求每题最优解。

## 题型分类

- `array/` 数组、`hash-table/` 哈希表、`string/` 字符串
- `two-pointers/` 双指针、`sliding-window/` 滑动窗口、`binary-search/` 二分
- `linked-list/` 链表、`stack-queue/` 栈与队列、`heap/` 堆
- `tree/` 树、`graph/` 图
- `backtracking/` 回溯、`greedy/` 贪心
- `dynamic-programming/` 动态规划、`bit/` 位运算
- `vibe-coding/` AI Coding / Vibe Coding 实战原题（非 LeetCode）

## 题解模板

```markdown
# 题目名（编号）

> 题型：xxx | 难度：Easy/Medium/Hard | 频率：高/中/低

## 题目描述

## 思路（讲出口：为什么这么想）

## 代码

## 复杂度分析（时间 + 空间）

## 易错点 / 相关题
```

## 题目索引

> 新增后登记一行，状态：待做 → 已写。

| 编号 | 题目 | 题型 | 难度 | 状态 |
| --- | --- | --- | --- | --- |
| - | （示例）Two Sum | 数组/哈希表 | Easy | 待做 |

<!-- ORGANIZED-INTERVIEW-IMPORT:START -->

## 已整理题目

| 题型 | 题目 | 频率 | 状态 |
| --- | --- | --- | --- |
| array | [不用 `Array.prototype.flat` 展平任意层级的嵌套数组](array/flatten-array.md) | 中 | 已写 |
| cache | [实现一个带过期时间的 LRU 缓存](cache/lru-ttl.md) | 高 | 已写 |
| concurrency | [用 TypeScript 实现限制并发数、保持结果顺序的异步任务调度器](concurrency/async-task-pool.md) | 高 | 已写 |
| hash-table | [两数之和（LeetCode 1）](hash-table/two-sum.md) | 高 | 已写 |
| sliding-window | [无重复字符的最长子串（LeetCode 3）](sliding-window/longest-substring-without-repeating.md) | 高 | 已写 |
| two-pointers | [盛最多水的容器（LeetCode 11）](two-pointers/container-with-most-water.md) | 高 | 已写 |
| binary-search | [搜索旋转排序数组（LeetCode 33）](binary-search/search-in-rotated-sorted-array.md) | 高 | 已写 |
| linked-list | [反转单链表（LeetCode 206）](linked-list/reverse-linked-list.md) | 高 | 已写 |
| linked-list | [两个链表的相交节点（LeetCode 160）](linked-list/intersection-of-two-linked-lists.md) | 高 | 已写 |
| tree | [二叉树的层序遍历（LeetCode 102）](tree/binary-tree-level-order.md) | 高 | 已写 |
| heap | [前 K 个高频元素（LeetCode 347）](heap/top-k-frequent-elements.md) | 高 | 已写 |
| graph | [岛屿数量（LeetCode 200）](graph/number-of-islands.md) | 高 | 已写 |
| array | [合并区间（LeetCode 56）](array/merge-intervals.md) | 高 | 已写 |
| dynamic-programming | [零钱兑换（LeetCode 322）](dynamic-programming/coin-change.md) | 高 | 已写 |

## Vibe Coding 原题

| 题型 | 原题文档 | 频率 | 状态 |
| --- | --- | --- | --- |
| 数据建模 | [扁平数组化成树](vibe-coding/flat-list-to-tree.md) | 高 | 已写 |
| 浏览器工程 | [弱网下上传 1G 文件](vibe-coding/resumable-large-file-uploader.md) | 高 | 已写 |
| 异步调度 | [100 个任务、最多 5 个并发](vibe-coding/concurrency-pool-original.md) | 高 | 已写 |

<!-- ORGANIZED-INTERVIEW-IMPORT:END -->

## 公开面经资料与筛选口径

资料检索入口为 Google，以下只记录能打开并核验到题目或题型的公开页面；“高频”表示在近期页面与历史大厂面经中重复出现，不能理解为任何公司的固定题单。

- [牛客：算法工程师精选面经合集](https://www.nowcoder.com/experience/645)：聚合 31 篇面经，页面可见近期 AI 全栈、Agent、算法工程师条目。
- [去哪儿旅行 AI 全栈开发面试](https://www.nowcoder.com/discuss/926507047238078464?sourceSSR=其他)：2026-09-01 条目，包含 AI 全栈岗位的工程与算法追问。
- [快手 AI 全栈二面](https://www.nowcoder.com/discuss/926528416512315392?sourceSSR=其他)：2026-09-07 条目，包含 Agent 工程落地与全栈基础追问。
- [百度 Agent 开发日常实习面经](https://www.nowcoder.com/feed/main/detail/bb8c28105f364770b57ff5eb5649cc60?sourceSSR=其他)：2026-09-07 条目，包含混合检索等 AI 工程问题。
- [字节 Agent 一面](https://www.nowcoder.com/feed/main/detail/612a1c20eea744a288b142f5b43f57e1?sourceSSR=其他)：2026-09-07 条目，作为 Agent 岗位背景题的补充样本。

最后更新：2026-09-08（Asia/Shanghai）。
