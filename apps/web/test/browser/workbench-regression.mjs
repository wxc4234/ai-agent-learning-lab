// 共用一轮隔离服务，依次验证删除/加载交互与既有聊天主流程。
// 两个脚本各自关闭浏览器；数据库由 run-isolated.py 统一清理。
await import('./task-delete.mjs');
await import('./workspace-task.mjs');
