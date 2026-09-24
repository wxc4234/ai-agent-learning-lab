// 保持隔离服务供交互验收；完成标记仅由本地验收流程写入。
import { access } from 'node:fs/promises';
const marker = new URL('../../output/playwright/layout/done', import.meta.url);
console.log('Layout fixture ready for interactive browser verification.');
const deadline = Date.now() + 650000;
while (Date.now() < deadline) {
    try { await access(marker); process.exit(0); } catch { /* 等待本地验收标记。 */ }
    await new Promise(resolve => setTimeout(resolve, 1000));
}
throw new Error('Interactive verification did not finish');
