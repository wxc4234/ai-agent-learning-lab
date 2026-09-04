# 后端持续生成，但前端长时间无数据后突然批量到达，如何分层定位？

> 主题：性能 / 分层排障 | 频率：高 | 关联项目：[ChatSearch AI](../../projects/chatsearch-render-backpressure.md)

## 考点（面试官在考察什么）

- 指标口径、瓶颈定位、优化方案与验证闭环。

## 核心答案（能直接讲出口的版本）

我会先从浏览器证据入手。打开 Network，保留日志并关闭缓存，检查 SSE 是否保持 Pending、响应是否为 `text/event-stream`、是否启用了 gzip/br、是否经过 Service Worker，以及传输大小是持续增长还是长时间不变后突然增长。

然后分别在 `reader.read()`、SSE 解析完成和进入渲染队列的位置记录时间与 chunk 大小：

- Network 和 `reader.read()` 都突然批量增长，说明数据在到达浏览器前被缓冲，重点检查服务端、网关和压缩。
- Network 持续增长但 `reader.read()` 迟迟不执行，检查读取循环是否停住或主线程是否被阻塞。
- `reader.read()` 持续返回但解析回调不触发，检查 TextDecoder 和 SSE 拆包逻辑。
- 数据已进入渲染队列但页面不更新，问题位于 San 更新、Markdown 渲染或串行消费队列。

同时用 Performance 录制卡顿区间，检查长任务和连续微任务是否让读流回调与页面绘制得不到执行机会；在 Application 中绕过 Service Worker，并确认读取循环没有错误地等待 Markdown 渲染完成后才继续 `read()`。

只有证据表明数据在浏览器之前被批量缓冲，才进一步通过直连服务、网关日志和关闭压缩做对照，区分服务端 flush、代理缓冲或压缩聚合问题。

## 排查速记

`Network 看传输 → read 看浏览器消费 → parser 看协议解析 → queue 看业务入队 → DOM 看渲染提交。`

## 容易说错的地方

- SSE 长时间保持 Pending 本身是正常现象，不能据此判断请求卡死。
- 网络数据到达浏览器不代表 JS 回调能立即运行，主线程阻塞同样会造成延迟。
- 应先确认数据停在哪一层，再让后端或网关配合排查。
