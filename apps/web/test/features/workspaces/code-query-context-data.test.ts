import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
    parseCodeContextJson, readCodeQueryContextRequest, readCodeQueryContext,
} from "../../../src/features/workbench/code-query-context-data.ts";
import type { CodeQueryContext, CodeQueryContextRequest } from "../../../src/features/workbench/code-query-context-data.ts";

// 使用后端纯构建/公开Schema实际生成的快照，避免手写一个恰好迎合TS校验的成功对象。
const fixture = JSON.parse(readFileSync(new URL("./code-query-context.fixture.json", import.meta.url), "utf8")) as {
    workspaceId: string; taskId: string; request: CodeQueryContextRequest; results: Record<string, CodeQueryContext>;
};
const { workspaceId, taskId, request: body } = fixture;
function snapshot(name = "basic"): CodeQueryContext { return structuredClone(fixture.results[name]); }
async function read(raw: unknown, request = body) { return readCodeQueryContext(raw, workspaceId, taskId, request); }

for (const name of Object.keys(fixture.results)) {
    test(`Python public contract accepted without losing audit fields: ${name}`, async () => {
        const raw = snapshot(name);
        const value = await read(raw);
        assert.deepEqual(value, raw);
        assert.notEqual(value, raw);
        assert.notEqual(value!.context.source_metadata, raw.context.source_metadata);
        value!.context.source_metadata.files[0].relative_path = "changed.py";
        assert.equal(raw.context.source_metadata.files[0].relative_path, "fixture.py");
    });
}
for (const [prompt, total] of [[null, null], [0, 0], [7, 9]] as const) {
    test(`query usage remains distinct from code usage: ${prompt}/${total}`, async () => {
        const raw = snapshot(); raw.prompt_tokens = prompt; raw.total_tokens = total;
        const result = await read(raw);
        assert.equal(result?.prompt_tokens, prompt); assert.equal(result?.total_tokens, total);
        assert.equal(result?.context.source_metadata.prompt_tokens, 40);
        assert.equal(result?.context.source_metadata.total_tokens, 50);
        assert.equal(result?.context.source_metadata.request_count, 2);
    });
}

const invalidBodies: unknown[] = [null, [], {}, { ...body, query: null }, { ...body, query: 1 },
    { ...body, query: true }, { ...body, query: "" }, { ...body, query: " \n" }, { ...body, query: "\u001c\u0085" },
    { ...body, query: "PRIVATE\u0000" }, { ...body, query: "\ud800" }, { ...body, query: "x".repeat(2001) },
    { ...body, query: "中".repeat(1366) }, { ...body, query: "🚀".repeat(1025) },
    { ...body, batch_id: "C".repeat(32) }, { ...body, batch_id: "c".repeat(32) + "\n" },
    { ...body, response_model: " PRIVATE " }, { ...body, response_model: "\u0085model" },
    { ...body, response_model: "PRIVATE\n" }, { ...body, response_model: "\ud800" },
    { ...body, response_model: "x".repeat(257) }, { ...body, response_model: null }, { ...body, query: body.query, user_id: 1 }];
for (const [index, value] of invalidBodies.entries()) {
    test(`strict query request rejects invalid input ${index}`, () => assert.equal(readCodeQueryContextRequest(value), null));
}
for (const query of [body.query, "x".repeat(2000), "🚀".repeat(1024), "\ufeff", "\u001ctext\u0085"]) {
    test(`query characters/UTF8 preserve permitted text: ${JSON.stringify(query.slice(0, 12))}`, () => {
        assert.deepEqual(readCodeQueryContextRequest({ ...body, query }), { ...body, query });
    });
}

type Change = { name: string; apply: (value: CodeQueryContext) => void };
const changes: Change[] = [
    { name: "query digest", apply: v => { v.query_sha256 = "d".repeat(64); } },
    { name: "query version", apply: v => { v.response_model = "other"; } },
    { name: "query requested model", apply: v => { v.requested_model = "other"; } },
    { name: "batch", apply: v => { v.context.batch_id = "d".repeat(32); } },
    { name: "workspace", apply: v => { v.context.source_metadata.workspace_id = "d".repeat(32); } },
    { name: "task", apply: v => { v.context.source_metadata.task_id = "d".repeat(32); } },
    { name: "space", apply: v => { v.context.space_id = "d".repeat(64); } },
    { name: "dimensions", apply: v => { v.context.dimensions = 4; } },
    { name: "reported usage missing", apply: v => { v.prompt_tokens = 1; } },
    { name: "usage inverted", apply: v => { v.prompt_tokens = 5; v.total_tokens = 4; } },
    { name: "usage unsafe integer", apply: v => { v.prompt_tokens = v.total_tokens = 2 ** 53; } },
    { name: "source unsafe integer", apply: v => { v.context.source_metadata.prompt_tokens = v.context.source_metadata.total_tokens = 2 ** 53; } },
    { name: "query count", apply: v => { Object.assign(v, { request_count: true }); } },
    { name: "input count", apply: v => { v.context.input_hit_count = 3; } },
    { name: "selected count", apply: v => { v.context.selected_hit_count = 1; } },
    { name: "character count", apply: v => { v.context.context_chars++; } },
    { name: "byte count", apply: v => { v.context.context_bytes++; } },
    { name: "server budget", apply: v => { v.context.budget.max_bytes++; } },
    { name: "recall count", apply: v => { v.context.recall_summary.searchable_chunk_count++; } },
    { name: "recall omission", apply: v => { v.context.recall_summary.omitted_by_top_k++; } },
    { name: "coverage", apply: v => { v.context.source_metadata.truncated = true; } },
    { name: "unknown coverage", apply: v => { v.context.source_metadata.incomplete_reasons = ["PRIVATE"]; } },
    { name: "file hash", apply: v => { v.context.source_metadata.files[0].sha256 = "d".repeat(64); } },
    { name: "duplicate file", apply: v => { v.context.source_metadata.files.push(v.context.source_metadata.files[0]); } },
    { name: "absolute path", apply: v => { v.context.source_metadata.files[0].relative_path = "/private/file.py"; } },
    { name: "relative traversal", apply: v => { v.context.source_metadata.files[0].relative_path = "a/../file.py"; } },
    { name: "reserved device", apply: v => { v.context.source_metadata.files[0].relative_path = "COM1.py"; } },
    { name: "file byte count", apply: v => { v.context.source_metadata.files[0].byte_count = 0; } },
    { name: "chunk text", apply: v => { v.context.selected_chunks[0].chunk.text = "PRIVATE"; } },
    { name: "chunk digest", apply: v => { v.context.selected_chunks[0].chunk.text_sha256 = "d".repeat(64); } },
    { name: "chunk identity", apply: v => { v.context.selected_chunks[0].chunk.chunk_id = "d".repeat(64); } },
    { name: "chunk coordinates", apply: v => { v.context.selected_chunks[0].chunk.end_column++; } },
    { name: "chunk lines", apply: v => { v.context.selected_chunks[0].chunk.line_count++; } },
    { name: "part index", apply: v => { v.context.selected_chunks[0].chunk.part_index = 2; } },
    { name: "unknown split reason", apply: v => { v.context.selected_chunks[0].chunk.split_reasons = ["PRIVATE"]; } },
    { name: "symbol name", apply: v => { v.context.selected_chunks[0].chunk.symbol.name = "other"; } },
    { name: "symbol range", apply: v => { v.context.selected_chunks[0].chunk.symbol.end_line = 0; } },
    { name: "rank order", apply: v => { v.context.selected_chunks.reverse(); } },
    { name: "distance order", apply: v => { v.context.selected_chunks[0].distance = 1; } },
    { name: "nonfinite distance", apply: v => { v.context.selected_chunks[0].distance = NaN; } },
    { name: "outside cosine distance", apply: v => { v.context.selected_chunks[0].distance = 3; } },
    { name: "extra top field", apply: v => { Object.assign(v, { api_key: "PRIVATE" }); } },
    { name: "extra nested field", apply: v => { Object.assign(v.context.selected_chunks[0].chunk.symbol, { api_key: "PRIVATE" }); } },
    { name: "extra metadata field", apply: v => { Object.assign(v.context.source_metadata, { root_path: "/private" }); } },
    { name: "duplicate selected ID", apply: v => { v.context.selected_chunks[1].chunk = structuredClone(v.context.selected_chunks[0].chunk); v.context.selected_chunks[1].distance = v.context.selected_chunks[0].distance; } },
];
for (const change of changes) {
    test(`public result rejects inconsistent ${change.name}`, async () => {
        const value = snapshot(); change.apply(value); assert.equal(await read(value), null);
    });
}
for (const field of ["scope", "batch_id", "embedding_space_id", "content_trust", "chunk_strategy", "chunk_policy", "snapshot_truncated", "snapshot_incomplete_reasons", "recall_omitted_by_top_k", "builder_omitted_hits", "chunks"]) {
    test(`rendered text cannot disagree with audit field ${field}`, async () => {
        const value = snapshot();
        const rendered = JSON.parse(value.context.context_text) as Record<string, unknown>;
        rendered[field] = field === "chunks" ? [] : "PRIVATE";
        value.context.context_text = JSON.stringify(rendered);
        value.context.context_chars = [...value.context.context_text].length;
        value.context.context_bytes = new TextEncoder().encode(value.context.context_text).length;
        assert.equal(await read(value), null);
    });
}
for (const [name, change] of [
    ["missing omission rank", (v: CodeQueryContext) => { v.context.omissions[0].source_rank = 0; }],
    ["unknown omission reason", (v: CodeQueryContext) => { v.context.omissions[0].reasons = ["PRIVATE"]; }],
    ["duplicate reason without previous chunk", (v: CodeQueryContext) => { v.context.omissions[0].reasons = ["duplicate_chunk"]; }],
] as const) {
    test(`omission audit rejects ${name}`, async () => { const value = snapshot("omitted"); change(value); assert.equal(await read(value), null); });
}

for (const source of ['{"a":1,"a":2}', '{"a":1,"\\u0061":2}', '{"x":{"a":1,"a":2}}',
    '{"a":}', '[1,]', '{"a":1,}', '01', 'true false', 'NaN', '1e9999', '1.0000000000000001', '9007199254740990.9', '1e-400', '\ufeff{}', '"\\ud800"', '"\\udc00"', ' '.repeat(2) + '[', '['.repeat(18) + '0' + ']'.repeat(18)]) {
    test(`bounded JSON parser rejects malformed/duplicate/unicode source ${JSON.stringify(source.slice(0, 20))}`, () => {
        assert.throws(() => parseCodeContextJson(source), /invalid_code_context_json/);
    });
}
test("JSON parser preserves strings, escaped keys, scalars and prototype-shaped keys safely", () => {
    const value = parseCodeContextJson('{"__proto__":{"x":1},"s":"🚀\\n","a":[false,null,1.25,-2e-2]}') as Record<string, unknown>;
    assert.equal(Object.getPrototypeOf(value), null);
    assert.equal(value.s, "🚀\n");
    assert.deepEqual(value.a, [false, null, 1.25, -0.02]);
    assert.equal(Object.hasOwn(value, "__proto__"), true);
});
test("JSON numeric syntax preserves mathematical integers and finite fractional distances", () => {
    assert.deepEqual(parseCodeContextJson('[1.0,100e-2,123.45e2,0e9999,1e-4]'), [1, 1, 12345, 0, 0.0001]);
});
