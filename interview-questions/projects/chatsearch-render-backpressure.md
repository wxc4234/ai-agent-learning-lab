# ChatSearch：流式渲染背压

> **难点一句话：**SSE 负责“数据已经到达”，DOM 负责“用户已经看到”，二者速度和完成时机不同，需要在中间建立一个可确认、不会永久阻塞的消费队列。

## 1. 业务背景

AI 回答不是纯文本。服务端会按顺序返回多种 Block：

- 思考过程；
- Markdown 正文；
- 引用和溯源；
- 图片、视频、图表；
- 代码块和文件；
- 动态加载的业务卡片；
- 结束信号和非展示控制包。

有的组件同步渲染，有的组件需要打字动画，有的要异步加载代码或资源。SSE 每几十毫秒就可能到一个包，但一个 Markdown 包的打字过程可能持续更久。

## 2. 难点具体发生在哪里

### 问题一：网络顺序正确，视觉顺序仍可能错误

如果收到包就直接更新 DOM，会出现：

```text
网络顺序：Markdown → 引用 → 图片卡片 → endTurn
视觉结果：图片卡片已经出现，Markdown 还在打字
```

数据顺序没有错，但用户看到的顺序错了。

### 问题二：SSE 结束时组件可能仍在工作

最后一个 `endTurn` 到达只能说明服务端不再发包，不能说明：

- Markdown 打字完成；
- 动态组件加载完成；
- 图片尺寸计算完成；
- 引用已经插入正文；
- 最后一个组件已经发出 `render-finished`。

如果此时立即触发 `answer-end`，暂停按钮、追问词和反馈区会提前出现。

### 问题三：连续 Markdown 包会造成组件爆炸

模型可能把一段正文拆成很多小包。如果每包创建一个 Markdown 组件，会产生大量 San 组件、重复解析 Markdown，并且段落之间容易出现不自然的断裂。

### 问题四：不是所有包都会自然产生完成事件

空 Markdown、引用控制包、结束包、append 目标不存在、动态组件加载失败等情况，可能没有真实组件可以发 `typing-finished`。如果队列只等待组件事件，就会永久卡在当前包，后续内容全部无法展示。

## 3. 为什么这个问题难

这是一个典型的生产者—消费者速率不匹配问题：

```text
生产者：SSE，速度由网络和模型决定
消费者：DOM/组件，速度由打字、异步资源和设备性能决定
```

浏览器没有提供“整个回答已经视觉完成”的统一事件。不同组件的完成语义也不一致：Markdown 是 `typing-finished`，普通卡片是 `render-finished`，部分控制包根本不渲染。因此必须在应用层定义统一的 ack 规则。

## 4. 解决方案：带确认机制的消费队列

我在 SSE 和组件树之间增加 `queue`：

```text
pushBlock(block)
      ↓
放入 queue
      ↓ 前一包已完成才允许消费
findNextBlockAndRender()
      ↓
新建组件 / 追加内容 / 更新已有组件 / 处理控制包
      ↓
typing-finished 或 render-finished
      ↓
handleBlockRenderFinished() 统一 ack
      ↓
继续消费下一包
```

关键不是“有一个数组”，而是：**任意时刻只允许一个逻辑 Block 占用消费权；它明确完成以后，下一个 Block 才能继续。**这就是应用层背压。

## 5. 关键实现细节

### 第一层：入口统一编号和入队

每个 Block 进入时先格式化并分配递增 `_index`，然后放入队列。只有 `prevBlockRenderFinished=true` 时才启动下一轮消费，防止多个异步分支同时操作组件树。

### 第二层：相邻同类包增量合并

相邻 Markdown 不重复创建组件，而是找到最后一个 Markdown 实例并调用 `appendContent()`：

```text
markdown("你好")
markdown("，这是")   → 同一个组件内容变为“你好，这是”
markdown("答案")
```

同时更新累计 Block 数据，保证后续插入引用时能够基于完整 Markdown 内容计算位置。

Wise 端还会对队列中的可合并包做批量合并，减少频繁渲染；PC 打字速度和产品表现不同，因此保留不同策略。这说明性能优化不能只看吞吐，还要兼顾最终交互节奏。

### 第三层：复用组件实例，不把完整正文反复绑定给父组件

大段文本最容易犯的错误是：每来一包都执行 `fullText += delta`，再把不断变大的 `fullText` 写回回答列表。这样会让父组件、Block 列表和 Markdown 子组件反复参与数据更新，Markdown 还可能从头解析整篇正文。

当前主链路没有这样做。连续 `ai-markdown` 包命中同一 Block 后，会直接复用已有组件实例：

```typescript
const markdownRef = this.ref(`ai-markdown${lastIndex}`);
markdownRef.appendContent(delta);

// 这份累计内容用于引用定位、复制和最终数据，不用它重新挂载组件
accBlocks[lastIndex].data.content += delta;
```

因此需要区分两条数据：

```text
累计业务数据：保存完整答案，供引用、复制、历史和最终收口使用
增量渲染数据：只把本次 delta 交给已经挂载的 Markdown 实例
```

San 的数据更新是按访问路径传播的；`cacheList`、`queue` 等内部状态也没有直接绑定为整段正文模板。新增一种 Block 时才通过 `accBlocks.push()` 挂载一个新组件；连续 Markdown 只调用子组件方法，不替换整个 `answerList`，也不销毁并重建已经完成的 Block。

这并不等于“完全没有重渲染”。Markdown 当前尾部仍然需要解析和提交 DOM，相关状态也会更新；优化目标是把更新范围限制在**当前活动 Block 的新增内容**，而不是让整个回答组件树跟随每个 SSE 包刷新。

### 第四层：Markdown 内部还有一层打字队列和尾段渲染

`appendContent(delta)` 进入 Cosmic Markdown 后不会无条件立即解析。组件内部维护：

| 字段 | 作用 |
|---|---|
| `_typingList` | 等待显示的文本增量队列 |
| `modeAllTimer` | 当前是否已有一批内容正在渲染，防止并发执行 |
| `_typingEndText` | 已接收的完整文本，用于最终停止和收口 |
| `_storeText` | 当前仍可能变化、需要重新解析的尾段文本 |
| `_renderingDom` | 当前尾段对应的 DOM 容器 |

核心逻辑接近：

```typescript
function appendContent(delta: string) {
    typingList.push(delta);
    consumeMarkdownQueue();
}

async function consumeMarkdownQueue() {
    // 已有任务时只入队，不并发解析
    if (modeAllTimer || isStopped || typingList.length === 0) {
        return;
    }

    modeAllTimer = true;
    const delta = typingList[0];
    typingEndText += delta;

    // 普通段落按最后一个换行切分，只重算仍可能变化的尾段
    const {before, after} = splitByLastNewline(delta);
    storeText += before;
    await marklang.renderToElementAsync(normalize(storeText), renderingDom);

    if (canFreezeCurrentParagraph(renderingDom, storeText)) {
        renderingDom = appendNewTailContainer();
        storeText = after;
        if (after) {
            await marklang.renderToElementAsync(normalize(after), renderingDom);
        }
    }

    await delay(typingSpeed);
    typingList.shift();
    modeAllTimer = null;

    if (typingList.length > 0) {
        consumeMarkdownQueue();
    }
    else {
        fire('typing-finished');
    }
}
```

这里有三项性能收益：

1. SSE 突发到达时先积累到 `_typingList`，已有渲染任务不会并发执行；
2. 打字间隔控制 DOM 提交频率，网络包数量不再等于页面更新次数；
3. 普通 Markdown 段落完成后会被冻结到独立 DOM，后续主要重新解析当前尾段，而不是每次从答案开头解析到结尾。

当前兜底打字参数也区分平台：PC 默认间隔为 10ms；Wise 普通兜底为 30ms，特殊 `typingHideMask === 'all'` 配置会放宽到 300ms。这个值不是越小越好：过小会增加解析和布局频率，过大又会让用户感觉输出停顿，因此它本质上是吞吐和观感的平衡。

### 第五层：支持异步回填已有组件

部分包不是新增组件，而是更新之前的组件，例如图片结果、引用数据或卡片的后置数据。协议通过 `appendTo + id` 定位目标 Block：

```text
appendTo：目标组件类型
id：目标业务组件 id
```

如果目标存在，则调用 `updateData()`；目标不存在时不能继续等待一个永远不会发生的事件，而要主动完成当前包并继续队列。

### 第六层：统一完成确认

所有组件的 `typing-finished` 和 `render-finished` 最终都汇入 `handleBlockRenderFinished()`：

1. 使用 `_renderFinished` 保证一个包只确认一次；
2. 向上抛出 `fragment-render-finished`；
3. 设置 `prevBlockRenderFinished=true`；
4. 队列非空时继续消费；
5. 队列为空且已经收到结束包时，才通知整个 `ai-entry` 完成。

结束条件因此变成：

```text
Entry 完成 = 已收到结束包 && queue 为空 && 当前 Block 已 ack
```

### 第七层：所有非标准路径都必须主动 ack

我逐类检查了不会自然发完成事件的分支：

| 分支 | 处理方式 |
|---|---|
| 空 Markdown | 主动调用完成回调 |
| 图片进度包 | 更新进度后主动完成 |
| append 目标不存在 | 放弃当前更新并主动完成 |
| 非渲染控制包 | 处理状态后主动完成 |
| 动态组件加载异常 | 只降级当前包，并释放队列 |
| 用户中断 | 停止打字并统一派发最终完成 |

这里最重要的不变量是：**每个进入队列的 Block 最终必须且只能获得一次 ack。**

### 第八层：组件切换时正确结束上一个组件

从 Markdown 切换到图片或卡片时，需要通知上一个组件 `updateEnd()`，并停止最后一个打字组件。否则旧组件可能继续打字，与新组件同时更新；停止过早又可能造成正文重复或引用不可点击，所以组件切换和 stop 时机也要纳入队列时序。

## 6. 当前方案仍可能在哪里卡顿

不能把这套机制讲成“保证永远不卡”。它解决的是**网络突发直接放大成组件更新风暴**，但 Markdown 解析和 DOM 操作仍发生在主线程，当前实现还存在以下边界：

- 一个超长段落始终没有换行时，`_storeText` 不能冻结，当前尾段会越来越大；
- 表格和 `ml-data` 指令为了保证语法完整，不走普通段落冻结逻辑；
- 长代码块的语法高亮、数学公式和复杂指令组件可能形成长任务；
- Markdown 为计算高度会读取 `getBoundingClientRect`、`offsetHeight`、`scrollHeight`，如果和样式写入交错，可能触发强制同步布局；
- 当前主消费链路主要依靠队列、合包和 `setTimeout` 节奏控制，并没有把 Markdown 解析整体迁移到 Web Worker；
- 回答持续很长、历史轮次很多时，DOM 总节点数仍会增长。

所以准确口径应是：**当前实现显著降低更新频率、限制更新范围并提供背压，但不是数学意义上的零卡顿保证。**

如果继续优化，我会按下面的顺序推进：

1. 增加 `queueLength`、`queuedChars` 和单批 `renderCost` 监控；队列超过高水位时扩大合批，必要时关闭逐包动画并快速追平；
2. 把固定时间节流改成时间预算调度：每帧只消费不超过约 6～8ms 的解析/DOM 任务，主动让出主线程；
3. 保留“已完成语法块 + 可变尾块”的增量 AST，避免长代码块或长列表反复全量解析；
4. 将纯 Markdown 词法分析、代码高亮等可序列化计算迁到 Worker，主线程只接收解析结果并分批提交 DOM；
5. 对历史轮次使用虚拟列表或 `content-visibility`，当前生成轮保持常驻，减少长会话的布局范围；
6. 用 Long Task、INP、单包渲染耗时、队列水位和“网络完成到视觉完成”的时间差验证效果，而不是只看字符打印速度。

这里要注意：单纯套一个 `requestAnimationFrame` 不能解决问题。如果一帧里仍解析完整篇 Markdown，照样会超过 16.7ms；真正重要的是合批、缩小重算范围和限制每次主线程任务的工作量。

## 7. 边界场景推演

### SSE 已结束，但队列还有三包

只记录 `lastBlockRender=true`，继续按顺序消费；直到最后一个包 ack 且队列为空，才派发整个 Entry 的 `render-finished`。

### 同一个组件错误地发两次完成事件

使用 Block 上的 `_renderFinished` 幂等标记，只承认第一次完成，避免队列被连续推进两次而跳包。

### 动态卡片代码加载失败

捕获当前卡片加载异常，当前包降级并 ack，后续 Markdown 和结束包仍然可以继续处理，不能让单个卡片拖死整轮回答。

## 8. 我重点保证的系统不变量

- SSE 包进入顺序就是逻辑消费顺序；
- 任意时刻只有一个逻辑 Block 等待完成；
- 连续内容尽量复用组件，不无上限创建实例；
- 网络包数不直接等于组件挂载次数或整篇 Markdown 重算次数；
- 非渲染包和异常包也必须释放队列；
- 一个 Block 最多 ack 一次；
- SSE 结束不能跳过剩余渲染任务；
- `answer-end` 发生时，最后一个可视内容已经 settled。

## 9. 项目价值

这套机制把网络速度和页面渲染速度解耦，使复杂结构化回答在快网、弱性能设备和动态组件异常时，都能保持正确的展示顺序与结束时机。

## 10. 面试回答口径

> 我们没有让每个 SSE 包直接执行 `fullText += delta` 再更新整棵回答组件树，而是在网络和 DOM 中间做了分层削峰。`ai-entry` 先用 `queue、curBlock、prevBlockRenderFinished` 串行消费，Wise 端还会按组件类型和 sectionId 合并积压包；连续 Markdown 复用同一个组件，通过 ref 调 `appendContent(delta)`，只有遇到新类型 Block 才挂载新组件。
>
> Markdown 内部还有 `_typingList` 和 `modeAllTimer`，正在解析时新内容只入队，不并发操作 DOM。普通段落完成后会冻结旧 DOM，后续主要解析仍可能变化的尾段，所以网络包数量不等于组件创建次数，也不等于整篇 Markdown 的重算次数。每批完成后通过 `typing-finished` ack，才允许上层消费下一批，这就是渲染背压。
>
> 这套方案能显著减少组件重建、Markdown 解析和 DOM 提交频率，但我不会说绝对不卡：超长代码块、表格、公式仍在主线程，可能产生 Long Task。进一步会增加队列高水位和渲染耗时监控，按帧预算合批，把纯解析和代码高亮迁到 Worker，并对历史轮次做虚拟化。最终我关注的是 INP、Long Task、队列峰值以及网络结束到视觉完成的延迟，而不只是打字速度。

---
