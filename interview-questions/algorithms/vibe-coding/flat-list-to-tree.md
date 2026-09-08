# Vibe Coding 原题：扁平数组化成树

> 题型：数据建模 / 哈希表 | 难度：Easy → Medium | 频率：高（AI 全栈实习面经）

## 原题（公开面经转述）

给定一组扁平节点，每项包含 `id` 和 `parentId`，请把它们转换成嵌套树结构。要求保留同级节点的输入顺序，并说明根节点、孤儿节点和重复 `id` 如何处理。

公开面经中的原始表述是“扁平数组化成树（Easy）”，不同面试官可能会追加“输入无序”“支持多棵树”或“不能修改原数组”等条件。以下实现采用：`parentId === null` 或不存在时作为根；孤儿节点挂到根级并记录告警；重复 `id` 直接抛错。

## 考点（面试官在考察什么）

- 一次遍历建立索引，避免对每个节点反复扫描父节点。
- 输入顺序、孤儿节点和重复 ID 的业务语义。
- 是否能把“写完代码”扩展成可验证的接口契约。

## 参考实现（TypeScript）

```ts
type FlatNode = {
    id: string;
    parentId: string | null;
    name: string;
};

type TreeNode = FlatNode & { children: TreeNode[] };

function flatListToTree(nodes: FlatNode[]): TreeNode[] {
    const byId = new Map<string, TreeNode>();

    for (const node of nodes) {
        if (byId.has(node.id)) {
            throw new Error(`duplicate id: ${node.id}`);
        }
        byId.set(node.id, {...node, children: []});
    }

    const roots: TreeNode[] = [];
    for (const node of nodes) {
        const current = byId.get(node.id)!;
        const parent = node.parentId ? byId.get(node.parentId) : undefined;

        if (parent) {
            parent.children.push(current);
        }
        else {
            roots.push(current);
        }
    }

    return roots;
}
```

## 复杂度与边界

- 时间复杂度 O(n)，空间复杂度 O(n)。
- 如果题目要求严格拒绝孤儿节点，把“挂根级”改成收集后抛错即可。
- 如果允许循环父子关系，需要再做 DFS 状态标记，发现环时返回可读错误。

## 追问清单

1. 输入无序时是否仍然成立？为什么？
2. 如何返回每个节点的完整路径（例如 `root/a/b`）？
3. 如何增量插入一个节点，而不重建整棵树？

## 来源

- [牛客：算法工程师精选面经合集](https://www.nowcoder.com/experience/645)（聚合页中的“抖音- AI 全栈工程师实习 一面二面凉经”，2026-03-19 条目；检索于 2026-09-08）
