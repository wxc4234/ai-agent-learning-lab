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
| web | [服务端登录会话：摘要、过期、撤销与事务](web/login-sessions.md) | 复习优先级：高 | 参考答案已整理、尚未模拟；含仓储、签发、Cookie/CSRF、身份解析、依赖注入/Session 生命周期与登出撤销/清 Cookie；含聊天/历史所有权、缓存授权、并发唯一键争用及事务测试证据 |

<!-- ORGANIZED-INTERVIEW-IMPORT:START -->

## 已整理题目

| 主题 | 题目 | 频率 | 状态 |
| --- | --- | --- | --- |
| network | [Koa BFF 如何流式转发上游 SSE，并处理背压与连接取消？](network/koa-sse-proxy.md) | 中 | 已写 |
| web | [一次 Agent 运行需要记录什么，如何按 `run_id` 还原时间线？](web/agent-run-observability.md) | 高 | 已写 |

<!-- ORGANIZED-INTERVIEW-IMPORT:END -->
