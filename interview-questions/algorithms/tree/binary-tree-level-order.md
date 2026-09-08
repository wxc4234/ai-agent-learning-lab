# 二叉树的层序遍历（LeetCode 102）

> 主题：二叉树 / BFS | 难度：Medium | 频率：高

## 考点（面试官在考察什么）

- 队列的层边界控制，以及是否能从遍历顺序自然扩展到最短路径问题。

## 核心答案（能直接讲出口的版本）

队列保存待处理节点。每一轮先记录当前队列长度，这个长度就是当前层节点数；处理完这一批后再进入下一层。

```ts
type TreeNode = {
    val: number;
    left: TreeNode | null;
    right: TreeNode | null;
};

function levelOrder(root: TreeNode | null): number[][] {
    if (!root) {
        return [];
    }

    const result: number[][] = [];
    const queue: TreeNode[] = [root];
    let head = 0;

    while (head < queue.length) {
        const levelSize = queue.length - head;
        const level: number[] = [];

        for (let i = 0; i < levelSize; i++) {
            const node = queue[head++];
            level.push(node.val);
            if (node.left) queue.push(node.left);
            if (node.right) queue.push(node.right);
        }

        result.push(level);
    }

    return result;
}
```

## 复杂度分析

- 时间复杂度：O(n)。
- 空间复杂度：O(w)，`w` 为树的最大宽度；返回结果本身占 O(n)。

## 易错点 / 相关题

- 用数组头部 `shift()` 会导致额外的 O(n) 移动，面试中建议用 `head` 下标。
- 相关题：二叉树最大/最小深度、锯齿形层序遍历、二叉树右视图。
