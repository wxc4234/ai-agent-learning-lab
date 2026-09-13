# 服务端登录会话如何保存、过期与撤销？

> 复习优先级：高 | 关联项目：AI Agent Learning Lab | 参考答案已整理、尚未模拟

## 参考答案与设计取舍

SQLAlchemy Session 管理数据库工作单元，Conversation 保存聊天业务，LoginSession 保存登录凭证对应的服务端状态。用户 external_id 是业务身份，不能作为认证凭证。

后续签发服务应生成高熵安全随机令牌，只将 SHA-256 摘要入库，原始令牌交给客户端。密码熵较低，需要 Argon2id 等慢哈希；足够随机的令牌可以使用快速摘要检索。摘要存储降低数据库泄露后直接重放令牌的风险，不能替代传输保护与日志脱敏。本课只完成存储，尚无签发服务或 Cookie。

有效会话要求摘要匹配、created_at <= now、expires_at > now、revoked_at IS NULL。到期瞬间即失效；要求带时区时间并转换 UTC。数据库 CHECK 约束限制过期晚于创建、撤销不早于创建、摘要格式，唯一索引拒绝重复摘要。

撤销使用条件 UPDATE ... RETURNING：摘要匹配、已创建、尚未撤销时写入 revoked_at。重复调用返回 false，保留首次时间；已过期记录仍可标记撤销。单条条件更新减少先查再改的竞态窗口；当前测试验证顺序幂等，未做并发压测。

仓储只 add/flush，不 commit；由上层服务统一控制事务。flush 成功不代表最终提交成功。服务端存储便于集中撤销，代价是认证需要查询状态，后续引入缓存还需权衡一致性。

## 失败场景

- 撤销误用 expires_at > now：过期记录无法标记，重复撤销可能覆盖原时间。
- 有效查询使用 expires_at >= now：到期瞬间仍有效。
- 仓储自行提交：外层失败无法原子回滚。
- 只有 Python 校验：其他写入路径可以绕过，因此仍需数据库约束。
- 将存储当作完整认证：Cookie 属性、BFF 转发、CSRF、鉴权和登出仍需落实。

## 项目证据

- `apps/api/app/models.py` 与 `apps/api/app/repositories/login_session_repository.py`：关联用户、唯一摘要、时间约束、创建、查询、撤销。
- `apps/api/tests/test_login_session_repository.py`：28 条 PostgreSQL 测试覆盖成功、失败、时间边界、时区以及真实提交/回滚。
- `apps/api/tests/test_login_session_migration.py`：隔离测试库演练升级、降级、再升级，与模型比较一致，原五张业务表逐行不变。降级删除登录会话表，仅在测试库演练。
- `apps/api/migrations/versions/c83f20a915bd_add_login_sessions.py`：新增登录会话表及索引。

2026-09-13：后端 335 条测试通过，Ruff 通过；本课模型、仓储、迁移和测试 Pyright 零错误、零警告。开发库升级至 c83f20a915bd，已有业务数据逐行不变。
