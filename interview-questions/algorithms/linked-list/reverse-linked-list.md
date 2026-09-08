# 反转单链表（LeetCode 206）

> 主题：链表 | 难度：Easy | 频率：高

## 考点（面试官在考察什么）

- 指针修改顺序、空链表和单节点边界；常见追问是递归实现。

## 核心答案（能直接讲出口的版本）

反转当前节点前，先保存原来的后继节点；然后让当前节点指向前驱，最后整体向后移动两个指针。

```ts
class ListNode {
    constructor(
        public val = 0,
        public next: ListNode | null = null
    ) {}
}

function reverseList(head: ListNode | null): ListNode | null {
    let previous: ListNode | null = null;
    let current = head;

    while (current) {
        const next = current.next;
        current.next = previous;
        previous = current;
        current = next;
    }

    return previous;
}
```

## 复杂度分析

- 时间复杂度：O(n)。
- 空间复杂度：O(1)；递归版本为 O(n) 调用栈。

## 易错点 / 相关题

- 不保存 `current.next` 就改指针，会丢失剩余链表。
- 相关题：K 个一组翻转链表、两两交换链表节点、回文链表。
