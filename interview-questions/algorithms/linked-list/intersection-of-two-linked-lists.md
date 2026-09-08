# 两个链表的相交节点（LeetCode 160）

> 主题：链表 / 双指针 | 难度：Easy | 频率：高

## 考点（面试官在考察什么）

- 不使用额外集合时，如何消除两条链表长度差造成的偏移。

## 核心答案（能直接讲出口的版本）

指针 A 走完链表 A 后切到链表 B，指针 B 走完链表 B 后切到链表 A。两者最终走过相同的 `a + b` 路径：有交点就在那里相遇，没有交点就同时为 `null`。

```ts
function getIntersectionNode(
    headA: ListNode | null,
    headB: ListNode | null
): ListNode | null {
    let pointerA = headA;
    let pointerB = headB;

    while (pointerA !== pointerB) {
        pointerA = pointerA ? pointerA.next : headB;
        pointerB = pointerB ? pointerB.next : headA;
    }

    return pointerA;
}
```

## 复杂度分析

- 时间复杂度：O(a + b)。
- 空间复杂度：O(1)。

## 易错点 / 相关题

- 判断相交必须比较节点引用，不能只比较 `val`。
- 相关题：链表环入口、删除倒数第 N 个节点。
