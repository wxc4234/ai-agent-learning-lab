# 后端

全栈底座之一。以本项目技术栈（Python / FastAPI / PostgreSQL / pgvector / Redis / Docker）为主线，兼顾通用后端面试题。

准备重点：面试常**从项目切入**（如「这个 RAG 为什么选 pgvector」「会话怎么落库」），抓与 **Agent 后端**强相关的原理（数据库 / 缓存 / 并发 / API 设计），传统深度八股（JVM、手写锁）可少放。

## 主题分类

- `language/` 语言（Python 核心：GIL/协程/生成器/内存管理/装饰器）
- `database/` 数据库（PostgreSQL/MySQL 索引·事务·隔离级别；Redis 数据结构·缓存·持久化；pgvector 向量检索）
- `network/` 网络（HTTP/TCP/三次握手/长连接/WebSocket·SSE）
- `os/` 操作系统（进程线程/内存/IO 模型）
- `concurrency/` 并发与异步（asyncio/线程池/进程池/锁）
- `distributed/` 分布式（一致性/消息队列/缓存一致性/幂等）
- `web/` Web 框架（FastAPI 依赖注入/中间件/异步/OpenAPI）

## 题解模板

```markdown
# 题目

> 主题：xxx | 频率：高/中/低 | 关联项目：xxx

## 考点

## 核心答案（讲取舍）

## 结合项目怎么讲

## 追问清单

## 延伸 / 坑
```

## 题目索引

| 主题 | 题目 | 频率 | 状态 |
| --- | --- | --- | --- |
| - | （示例）MySQL 索引原理 | 高 | 待做 |
| web | [密码哈希、注册事务、登录凭证与 HTTP 脱敏](web/password-hashing.md) | 复习优先级：高 | 参考答案已整理、尚未模拟；含中文用户名及凭证校验证据 |
| web | [服务端登录会话：摘要、过期、撤销与事务](web/login-sessions.md) | 复习优先级：高 | 参考答案已整理、尚未模拟；含本机身份/内部访问凭证、仓储、签发、Cookie/CSRF、身份解析、依赖注入/Session 生命周期与登出撤销/清 Cookie；含聊天/历史所有权、缓存授权、并发唯一键争用、事务测试、提交前结果快照、HTTP 提交后失败、BFF 响应白名单/注册结果不确定性及迁移账本/实际结构校准证据 |

<!-- ORGANIZED-INTERVIEW-IMPORT:START -->

## 已整理题目

| 主题 | 题目 | 频率 | 状态 |
| --- | --- | --- | --- |
| network | [Koa BFF 如何流式转发上游 SSE，并处理背压与连接取消？](network/koa-sse-proxy.md) | 中 | 已写 |
| web | [运行时间线、所有权与取消终态互斥](web/agent-run-observability.md) | 复习优先级：高 | 参考答案已整理、尚未模拟；含真实 PostgreSQL 行锁、回滚、Redis 提交后失败与 BFF/页面取消证据 |

| web | [Workspace 目录规范化与访问授权边界](web/workspace-directory.md) | 复习优先级：高 | 参考答案已整理、尚未模拟；含目录校验、兼容迁移、绑定事务/并发行锁、HTTP 提交后失败、BFF 响应校验及系统目录选择、任务授权路径解析、跨平台规则和 TOCTOU 边界证据；含受限读取、描述符清理、替换测试及可信上下文/会话归属、执行器透传及缺失拒绝证据；含文件工具适配、可见能力过滤与安全错误；含流式聊天上下文装配、限量目录枚举、工具注册、截断语义及列目录后读取链路、单文件搜索坐标与输出预算、只读工具链PC闭环及刷新无重放证据、命令请求/策略分离及结果一致性、有界输出与UTF-8分块/双截断、异步排空与取消调度、双路TaskGroup收尾与异常组、环境白名单及目标路径边界、Docker最小隔离与子孙进程停止证据、创建参数分层与不可变规格、创建响应身份/状态严格解析、Docker客户端超时取消/进程回收与daemon结果不确定边界、创建装配及响应丢失注入证据、只读恢复核对与已知ID防替换边界、显式非强制清理及删除结果不确定、启动前执行配置及双层环境复核、HostConfig隔离/资源/挂载策略、完整ID停止及daemon状态确认、启动复核/快退与取消停止收尾、退出码/OOM/daemon错误结果区分、attach有界增量拆帧与EOF边界；通用Sandbox执行器待实现 |

<!-- ORGANIZED-INTERVIEW-IMPORT:END -->

Task 专题（含创建事务、HTTP 提交后失败、前端防重边界、异步标题、详情 BFF 资源匹配、URL 恢复竞态、空任务删除锁竞争、禁止隐式重建、DELETE 结果未确认、UI 迟到结果隔离与运行列表授权分页/HTTP 错误边界/BFF 契约校验/历史列表请求隔离/历史事件契约/启动迁移职责/创建幂等请求键与删除保留）：[Task、Conversation 和 AgentRun 建模](web/task-model.md)，复习优先级高，参考答案已整理、尚未模拟。
