# 不用 `Array.prototype.flat` 展平任意层级的嵌套数组

> 主题：数组 / 递归 | 频率：中

## 考点（面试官在考察什么）

- 递归/迭代实现、边界和复杂度。

## 核心答案（能直接讲出口的版本）

## 实现思路

使用显式栈代替递归，避免数组层级过深导致调用栈溢出。数组元素倒序入栈、从栈顶正序消费，从而保持原有顺序；通过 `i in current` 跳过稀疏数组空位，同时保留显式的 `undefined`。

```ts
function flatten(input: any[]): any[] {
    const result: any[] = [];
    const stack: any[] = [input];

    while (stack.length) {
        const current = stack.pop();

        if (Array.isArray(current)) {
            for (let i = current.length - 1; i >= 0; i--) {
                if (i in current) {
                    stack.push(current[i]);
                }
            }
        }
        else {
            result.push(current);
        }
    }

    return result;
}
```

## 边界与复杂度

- 空数组返回空数组，对象、字符串、`null` 和显式 `undefined` 都作为普通元素保留。
- 不修改输入数组，并保持从左到右的元素顺序。
- 默认输入不存在循环引用；如需支持，应额外检测并抛错。
- 时间复杂度 O(n)，空间复杂度 O(n)，其中 n 为遍历到的数组节点和元素总数。
