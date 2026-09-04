# ChatSearch：双答案对比一致性

> **难点一句话：**一条 SSE 同时交叉返回两个答案，既要保证数据不串列，又要保证两列网络和渲染都结束以后，整轮副作用只执行一次。

## 1. 业务背景

双答案对比用于同时生成两个候选回答，让用户浏览并选择更喜欢的一列。它并不是“发两次请求、渲染两个卡片”，而是一条 SSE 在同一条流中交叉返回两个分支：

```text
SSE 包 1：compareIdx=0，答案 A 的 Markdown
SSE 包 2：compareIdx=1，答案 B 的思考过程
SSE 包 3：compareIdx=0，答案 A 的引用
SSE 包 4：compareIdx=0，答案 A endTurn
SSE 包 5：compareIdx=1，答案 B 的 Markdown
SSE 包 6：compareIdx=1，答案 B endTurn
```

因此，网络到包顺序、两列生成进度、用户当前浏览位置和 DOM 打字进度都是互相独立的。

## 2. 难点具体发生在哪里

### 问题一：数据写入列和用户浏览列不是同一个概念

`currentIndex` 代表用户当前正在浏览哪一列，它会随着用户切换答案而变化；`compareIdx` 才代表当前 SSE 包属于哪一列。

假设用户正在浏览答案 A，此时答案 B 的数据包到达。如果代码直接使用 `currentIndex`，B 的内容就会写进 A：

```text
用户操作：currentIndex 从 1 切换到 0
网络到包：compareIdx = 1

错误路由：写入 answerList[currentIndex] = answerList[0]
正确路由：写入 answerList[compareIdx]  = answerList[1]
```

### 问题二：分支完成不等于整轮完成

答案 A 先收到 `endTurn` 时，只能说明 A 的网络数据结束，不能说明 B 也结束，更不能立即触发整轮 `answer-end`。

如果沿用单答案逻辑，会造成：

- 暂停按钮提前消失；
- 偏好选择区域提前出现；
- 反馈卡、已读通知和完成日志提前执行；
- B 后续到包时，全局状态已经是 COMPLETE，数据被忽略或状态回退。

### 问题三：网络完成不等于页面完成

SSE 的最后一个包可能已经到达，但 Markdown 组件仍然在执行打字动画，图片或动态卡片也可能仍在加载。此时展示偏好选择区域，会让用户在答案尚未稳定时做选择。

### 问题四：完成逻辑存在重复执行风险

两列 SSE 结束、两列 DOM 渲染完成，会产生多个完成触发点。如果每个触发点都直接执行完成逻辑，就可能出现两次 `answer-end`、两次日志和两张反馈卡。

## 3. 为什么这个问题难

这里同时存在三类状态：

| 状态类型 | 典型字段 | 变化来源 |
|---|---|---|
| 网络状态 | `compareSseEndedMap`、`sseStatus` | SSE 包和 `endTurn` |
| 渲染状态 | `isFinished`、`isComplete` | 子组件 `render-finished` |
| 交互状态 | `viewingIndex`、`selectedIndex` | 用户切列和选择 |

任何一个状态都不能单独决定整轮是否完成。难点在于定义清楚状态之间的不变量，并让正常结束、用户停止、网络异常都收敛到同一套完成模型。

## 4. 解决方案

我把方案拆成五层：

```text
SSE 包到达
   ↓
按 compareIdx 路由到固定分支
   ↓
分别维护每列 SSE 与 DOM 状态
   ↓
Network Barrier + Render Barrier
   ↓
幂等执行整轮完成 → awaiting_selection
```

### 第一层：使用稳定的分支路由

服务端首次下发 `comparison` 配置时激活对比模式，并提前补齐 `answerList` 的分支槽位。随后所有数据包按照以下优先级选择目标列：

```text
compareIdx > 显式 targetIndex > currentIndex 兜底
```

对 `compareIdx` 还要做越界检查。异常索引直接丢弃，不能让它写入不存在的分支，更不能回退到用户正在浏览的列。

### 第二层：用单一状态机驱动 UI

我使用 `compareInfo` 作为对比模式的唯一状态源：

```text
normal
  ↓ 收到 comparison
generating
  ↓ 两列全部完成
awaiting_selection
  ↓ 用户选择
selected
```

- `viewingIndex`：用户当前正在看哪一列；
- `selectedIndex`：用户最终选择哪一列，未选择时为 `-1`；
- `state`：当前是生成中、等待选择还是已经选择。

页面是否展示翻页器、偏好按钮和交互区，都从 `compareInfo` 派生，避免多个布尔变量互相矛盾。

### 第三层：建立双重完成屏障

整轮完成必须同时满足：

```text
Network Barrier：所有预期分支都收到 endTurn
Render Barrier：每个分支最后一个 section 都完成 DOM 渲染

整轮完成 = Network Barrier && Render Barrier
```

网络侧用 `compareSseEndedMap` 记录各分支是否结束；渲染侧检查每个分支的 `isFinished` 和 `sseStatus`。不能只统计包数量，因为 Markdown 中间包也可能带局部 `isFinished`，必须结合 `state=generate-complete` 和 `endTurn` 判断真正的分支完成包。

### 第四层：分支完成与整轮完成分开

我把原来混在一起的完成逻辑拆成两级：

- `finishBranchAnswer()`：每个分支执行一次，只设置该列的完成态和互动数据；
- `finishAllAnswers()`：只有策略判定整轮完成时才执行；
- `completeCompareAnswer()`：双列专用收口，触发一次 `answer-end`、全局 COMPLETE 和等待选择状态。

SSE 完成和 DOM 完成都会调用 `completeCompareAnswer()` 尝试收口，但方法内部再次执行 `isCompareAllDone()`。一旦状态已经进入 `awaiting_selection`，后续调用直接返回，从而实现幂等。

### 第五层：异常路径也必须收敛

用户停止或网络异常时，两列不一定都收到完整的结束包，部分画布组件也可能永远不会再发完成事件。此时需要：

1. 同时停止两个分支的渲染；
2. 将未完成分支设置为 ABORT；
3. 补齐 `compareSseEndedMap`、`isFinished` 和 `isComplete`；
4. 将状态机收敛到 `awaiting_selection`，不能永久停在 `generating`；
5. 区分用户主动停止与异常中断，决定是否允许后续选择结果上报。

## 5. 我重点保证的系统不变量

- 一个 SSE 包只能写入它的 `compareIdx` 分支；
- 用户切列只影响展示，不能影响网络数据路由；
- 一个分支结束不能触发整轮结束；
- 整轮结束必须同时满足所有 SSE 结束和所有 DOM settled；
- `answer-end`、反馈卡、已读通知和完成日志整轮最多执行一次；
- 正常、停止和异常路径最终都不会卡在生成态。

## 6. 项目价值

这项工作的价值不是增加了一个双列 UI，而是解决了多分支异步系统的 exactly-once 问题：**数据不串列、状态不提前结束、完成副作用不重复、异常状态可收敛**。

## 7. 面试回答口径

> 双答案最难的地方是，一条 SSE 会交叉返回两个分支，而用户浏览列、网络写入列和最终选择列是三个不同概念。我首先用 compareIdx 做稳定数据路由，再用 compareInfo 统一维护 generating、awaiting_selection 和 selected。完成方面不能只看 SSE，也不能只看 DOM，所以我建立了 Network Barrier 和 Render Barrier，只有两列网络和渲染全部完成才执行整轮收口。最后把分支完成和整轮完成拆开，并让 SSE 完成和 DOM 完成统一进入一个带幂等守卫的方法，保证 answer-end、日志和反馈卡只执行一次。

---
