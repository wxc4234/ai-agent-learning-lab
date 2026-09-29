// 离线复核隔离诊断证据；不连接服务、不重放请求，也不推定故障根因。
import { open } from 'node:fs/promises';
import { resolve, basename } from 'node:path';
import { pathToFileURL } from 'node:url';

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const status = value => Number.isInteger(value) && value >= 100 && value <= 599;
const pathPattern = /^\/api\/workspaces\/[a-f0-9]{32}\/tasks\/[a-f0-9]{32}\/file-edit-proposals\/[a-f0-9]{32}(?:\/decision|\/write-grant(?:\/revoke)?)?$/;

export function reviewTrace({ runId, browser, bff, api, database, result, readErrors = [] }) {
    const gaps = [...readErrors];
    const requests = [];
    const arrays = { browser, bff, api };
    for (const [name, rows] of Object.entries(arrays)) {
        if (!Array.isArray(rows) || rows.some(row => !object(row))) {
            gaps.push(`${name}: invalid rows`);
            arrays[name] = [];
        }
    }
    const events = arrays.browser;
    const seen = new Set();
    for (const event of events) {
        if (typeof event.id !== 'string' || !/^request-[1-9]\d*$/.test(event.id)
            || !['GET', 'POST'].includes(event.method) || !pathPattern.test(event.path)
            || !status(event.status) || !status(event.expected)) {
            gaps.push('browser: invalid event');
            continue;
        }
        if (seen.has(event.id)) gaps.push(`${event.id}: duplicate browser id`);
        seen.add(event.id);
        // GET详情未插桩，只检查浏览器结果；POST必须有且仅有一条对应上游与API记录。
        const upstream = arrays.bff.filter(row => row.id === event.id);
        const backend = arrays.api.filter(row => row.id === event.id);
        const issues = [];
        if (event.method === 'POST') {
            for (const [name, rows] of [['bff', upstream], ['api', backend]]) {
                if (rows.length !== 1) {
                    issues.push(`${name}: expected one trace, found ${rows.length}`);
                    continue;
                }
                const row = rows[0];
                if (row.path !== event.path.slice(4) || row.method !== event.method) issues.push(`${name}: resource mismatch`);
                if (!status(row.status)) issues.push(`${name}: response status unknown`);
                if ((name === 'api' ? row.run : row.backend_run) !== runId) issues.push(`${name}: run mismatch`);
                if (row.status !== event.status) issues.push(`${name}: status differs from browser`);
                if (event.status >= 400 && (typeof event.data?.code !== 'string' || row.code !== event.data.code)) {
                    issues.push(`${name}: error code mismatch or missing`);
                }
            }
        }
        const observed404 = [
            ...(event.status === 404 ? ['browser'] : []),
            ...(upstream.some(row => row.status === 404) ? ['bff_upstream'] : []),
            ...(backend.some(row => row.status === 404) ? ['api'] : []),
        ];
        // 归属快照采集在响应之后，只提供辅助事实，不能还原拒绝发生时的事务状态。
        let ownership = null;
        if (backend.length === 1 && backend[0].status === 404) {
            const snapshot = backend[0].ownership_snapshot;
            const fields = ['workspace_exists', 'workspace_owned', 'task_linked', 'proposal_linked'];
            if (!object(snapshot) || fields.some(key => typeof snapshot[key] !== 'boolean')) {
                issues.push('api: ownership snapshot missing or invalid');
            } else ownership = Object.fromEntries(fields.map(key => [key, snapshot[key]]));
        }
        gaps.push(...issues.map(issue => `${event.id}: ${issue}`));
        requests.push({ id: event.id, expected: event.expected, observed: event.status,
            matched_expectation: event.status === event.expected, observed_404_at: observed404,
            unexpected_404: observed404.length > 0 && event.expected !== 404,
            correlated: event.method === 'POST' && issues.length === 0, ownership_after_response: ownership });
    }
    for (const name of ['bff', 'api']) {
        for (const row of arrays[name]) {
            // UI自行查询产生的无ID GET不属于显式写请求；未知写记录不能静默忽略。
            if (row.method === 'GET' && !row.id) continue;
            if (!events.some(event => event.id === row.id && event.method === 'POST')) gaps.push(`${name}: uncorrelated trace`);
        }
    }
    if (!object(result) || result.completed !== true || result.requests !== events.length
        || result.traced_writes !== events.filter(row => row.method === 'POST').length
        || events.length !== 10 || events.filter(row => row.method === 'POST').length !== 8) {
        gaps.push('run: fixed cold scenario incomplete');
    }
    // 这里只确认最终快照存在且结构有效，不把事后快照当作每次请求无副作用的证明。
    const databaseValid = object(database) && Array.isArray(database.proposals) && database.proposals.length > 0
        && Array.isArray(database.grants)
        && database.proposals.every(row => object(row) && typeof row.proposal_id === 'string'
            && typeof row.status === 'string' && typeof row.application_status === 'string'
            && typeof row.has_application_token === 'boolean')
        && database.grants.every(row => object(row) && typeof row.grant_id === 'string'
            && Number.isSafeInteger(row.revision) && row.revision > 0 && typeof row.enabled === 'boolean');
    if (!databaseValid) gaps.push('database: final snapshot missing or invalid');
    const anomaly = requests.some(row => !row.matched_expectation || row.unexpected_404);
    return {
        conclusion: anomaly ? 'unexpected_response_observed' : gaps.length ? 'evidence_incomplete' : 'recorded_scenario_matched',
        root_cause: 'not_established',
        evidence_gaps: [...new Set(gaps)], requests,
        final_database_snapshot: databaseValid ? 'present_not_transaction_proof' : 'unknown',
        boundary: 'Offline evidence only; no request replay; matching results do not prove the historical fault fixed.',
    };
}

export async function reviewDirectory(directory) {
    const readErrors = [];
    const read = async (name, lines = false) => {
        let handle;
        try {
            handle = await open(resolve(directory, name), 'r');
            if (!(await handle.stat()).isFile()) throw new Error('not a regular file');
            // 读取预算包含一个哨兵字节；文件在读取期间增长也不能绕过上限。
            const raw = Buffer.alloc(1024 * 1024 + 1);
            let size = 0;
            while (size < raw.length) {
                const { bytesRead } = await handle.read(raw, size, raw.length - size, null);
                if (!bytesRead) break;
                size += bytesRead;
            }
            if (size === raw.length) throw new Error('oversized');
            const text = new TextDecoder('utf-8', { fatal: true }).decode(raw.subarray(0, size));
            return lines ? text.trim().split('\n').filter(Boolean).map(line => JSON.parse(line)) : JSON.parse(text);
        } catch {
            readErrors.push(`${name}: unreadable, oversized or invalid JSON`);
            return null;
        } finally { await handle?.close(); }
    };
    const [browser, bff, api, database, result] = await Promise.all([
        read('browser.json'), read('bff.jsonl', true), read('api.jsonl', true),
        read('final-database.json'), read('result.json'),
    ]);
    return reviewTrace({ runId: basename(resolve(directory)), browser, bff, api, database, result, readErrors });
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
    if (process.argv.length !== 3) {
        console.error('Usage: node review-project-write-trace.mjs <trace-run-directory>');
        process.exitCode = 2;
    } else {
        const report = await reviewDirectory(process.argv[2]);
        console.log(JSON.stringify(report, null, 4));
        process.exitCode = report.conclusion === 'recorded_scenario_matched' ? 0
            : report.conclusion === 'unexpected_response_observed' ? 1 : 2;
    }
}
