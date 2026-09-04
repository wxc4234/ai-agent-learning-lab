# 纵向 Flex 对话页为什么会被长内容撑开，如何让滚动只发生在回答区？

> 主题：CSS / Flex | 频率：高

## 考点（面试官在考察什么）

- 布局约束、滚动容器与边界场景。

## 核心答案（能直接讲出口的版本）

我会先用 DevTools 确认是哪一层的 `scrollHeight` 超过容器，并重点检查 Flex 子项的 `min-height` 和 `min-width`。

纵向 Flex 中，中间区域即使设置了 `flex: 1`，默认 `min-height: auto` 仍可能让它受内容最小高度约束，拒绝收缩并把整页撑高。因此外层应固定为视口高度、使用纵向 Flex 并设置 `overflow: hidden`；顶部和底部不收缩；中间回答区设置 `flex: 1; min-height: 0; overflow-y: auto`。如果中间还有多层 Flex，每一层都要检查 `min-height: 0`。

横向溢出同理：Flex 子项默认 `min-width: auto`，代码块和表格可能让容器无法收窄，因此回答区及中间 Flex 容器需要 `min-width: 0`。普通文本允许长链接换行，代码块和表格在自己的容器内横向滚动，图片和画布限制最大宽度为容器宽度。

核心原则是：页面本身不滚动，回答区允许收缩并承担纵向滚动；宽内容只在各自内部处理横向滚动。

## 样式速记

```css
.page {
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

.header,
.input {
    flex-shrink: 0;
}

.answer {
    flex: 1;
    min-height: 0;
    min-width: 0;
    overflow-y: auto;
}

.code,
.table {
    max-width: 100%;
    overflow-x: auto;
}
```

## 容易说错的地方

- 只写 `flex: 1` 不一定能形成内部滚动区，通常还需要 `min-height: 0`。
- 多层 Flex 时，任一祖先拒绝收缩都可能继续把页面撑开。
- 整个回答区不应为宽表格横向滚动，横向滚动应尽量收口到具体内容块。
