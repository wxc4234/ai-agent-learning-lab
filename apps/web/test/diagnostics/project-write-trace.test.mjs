import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { reviewTrace, reviewDirectory } from '../browser/review-project-write-trace.mjs';

function fixture() {
    const path = `/api/workspaces/${'a'.repeat(32)}/tasks/${'b'.repeat(32)}/file-edit-proposals/${'c'.repeat(32)}`;
    const statuses = [200, 200, 201, 409, 200, 200, 200, 201, 409, 404];
    const browser = statuses.map((status, index) => ({
        id: `request-${index + 1}`, path: path + ([0, 5].includes(index) ? '' : '/decision'),
        method: [0, 5].includes(index) ? 'GET' : 'POST', expected: status, status,
        data: status >= 400 ? { code: status === 404 ? 'workspace_not_accessible' : 'project_write_grant_conflict' } : {},
    }));
    const api = browser.filter(row => row.method === 'POST').map(row => ({
        ...row, path: row.path.slice(4), run: 'run-1', code: row.data.code ?? null,
        ...(row.status === 404 ? { ownership_snapshot: {
            workspace_exists: true, workspace_owned: false, task_linked: true, proposal_linked: true,
        } } : {}),
    }));
    return { runId: 'run-1', browser, api, bff: api.map(row => ({ ...row, backend_run: 'run-1' })),
        database: { proposals: [{ proposal_id: 'proposal', status: 'approved', application_status: 'idle', has_application_token: false }], grants: [] },
        result: { completed: true, requests: 10, traced_writes: 8 } };
}

test('complete matching evidence includes expected 404 but never establishes root cause', () => {
    const report = reviewTrace(fixture());
    assert.equal(report.conclusion, 'recorded_scenario_matched');
    assert.deepEqual(report.evidence_gaps, []);
    assert.equal(report.root_cause, 'not_established');
    assert.equal(report.requests.at(-1).unexpected_404, false);
    assert.deepEqual(report.requests.at(-1).observed_404_at, ['browser', 'bff_upstream', 'api']);
});

const incomplete = {
    'missing API trace': data => data.api.shift(),
    'duplicate trace': data => data.bff.push(data.bff[0]),
    'duplicate browser ID': data => { data.browser[1].id = data.browser[0].id; },
    'wrong run': data => { data.api[0].run = 'another-run'; },
    'wrong BFF run': data => { data.bff[0].backend_run = 'another-run'; },
    'wrong resource': data => { data.api[0].path += '/other'; },
    'wrong method': data => { data.bff[0].method = 'GET'; },
    'unknown upstream outcome': data => { delete data.bff[0].status; data.bff[0].transport_error = true; },
    'status disagreement': data => { data.api[0].status = 500; },
    'error code disagreement': data => { data.api.at(-1).code = 'different'; },
    'missing ownership snapshot': data => { delete data.api.at(-1).ownership_snapshot; },
    'unknown ownership': data => { data.api.at(-1).ownership_snapshot.workspace_owned = null; },
    'unknown write': data => { data.api.push({ ...data.api[0], id: 'request-99' }); },
    'malformed event': data => { data.browser[0] = null; },
    'malformed status': data => { data.browser[0].status = '200'; },
    'missing database': data => { data.database = null; },
    'empty database': data => { data.database.proposals = []; },
    'invalid grant snapshot': data => { data.database.grants = [{ revision: 0 }]; },
    'unfinished scenario': data => { data.result.completed = false; },
    'false count': data => { data.result.traced_writes = 7; },
    'empty traces': data => { data.browser = []; data.bff = []; data.api = []; },
};
for (const [name, mutate] of Object.entries(incomplete)) {
    test(name, () => {
        const data = fixture();
        mutate(data);
        const report = reviewTrace(data);
        assert.equal(report.conclusion, 'evidence_incomplete');
        assert.ok(report.evidence_gaps.length > 0);
    });
}

test('unexpected API 404 remains visible even if browser received another status', () => {
    const data = fixture();
    data.api[0].status = 404;
    const report = reviewTrace(data);
    assert.equal(report.conclusion, 'unexpected_response_observed');
    assert.deepEqual(report.requests[1].observed_404_at, ['api']);
    assert.ok(report.evidence_gaps.length);
    assert.equal(report.root_cause, 'not_established');
});

test('browser unexpected 404 without downstream logs is an observation, not a layer diagnosis', () => {
    const data = fixture();
    data.browser[1].status = 404;
    data.api.shift(); data.bff.shift();
    const report = reviewTrace(data);
    assert.equal(report.conclusion, 'unexpected_response_observed');
    assert.deepEqual(report.requests[1].observed_404_at, ['browser']);
    assert.equal(report.requests[1].correlated, false);
});

test('untraced automatic GETs are allowed', () => {
    const data = fixture();
    data.api.push({ id: '', method: 'GET' });
    data.bff.push({ id: null, method: 'GET' });
    assert.equal(reviewTrace(data).conclusion, 'recorded_scenario_matched');
});

test('CLI and file reader handle complete, truncated, missing, and anomalous evidence', async () => {
    const root = await mkdtemp(join(tmpdir(), 'trace-review-'));
    const data = fixture();
    const runId = root.split('/').at(-1);
    for (const row of data.api) row.run = runId;
    for (const row of data.bff) row.backend_run = runId;
    const cli = new URL('../browser/review-project-write-trace.mjs', import.meta.url);
    const invoke = () => spawnSync(process.execPath, [cli.pathname, root], { encoding: 'utf8' });
    try {
        for (const [name, value] of [['browser', data.browser], ['result', data.result], ['final-database', data.database]]) {
            await writeFile(join(root, name + '.json'), JSON.stringify(value));
        }
        for (const [name, value] of [['api', data.api], ['bff', data.bff]]) {
            await writeFile(join(root, name + '.jsonl'), value.map(row => JSON.stringify(row)).join('\n') + '\n');
        }
        assert.equal(invoke().status, 0);
        data.browser[1].status = 404;
        await writeFile(join(root, 'browser.json'), JSON.stringify(data.browser));
        assert.equal(invoke().status, 1);
        data.browser[1].status = 200;
        await writeFile(join(root, 'browser.json'), JSON.stringify(data.browser));
        await writeFile(join(root, 'api.jsonl'), '{"id":');
        assert.equal(invoke().status, 2);
        assert.match((await reviewDirectory(root)).evidence_gaps.join(' '), /api.jsonl/);
        await writeFile(join(root, 'api.jsonl'), Buffer.alloc(1024 * 1024 + 1, 32));
        assert.match((await reviewDirectory(root)).evidence_gaps.join(' '), /api.jsonl/);
        await writeFile(join(root, 'api.jsonl'), Buffer.from([0xff]));
        assert.match((await reviewDirectory(root)).evidence_gaps.join(' '), /api.jsonl/);
        await rm(join(root, 'final-database.json'));
        assert.equal((await reviewDirectory(root)).final_database_snapshot, 'unknown');
    } finally { await rm(root, { recursive: true, force: true }); }
});
