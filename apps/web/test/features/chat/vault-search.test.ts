import assert from "node:assert/strict";
import test from "node:test";
import {
    parseVaultSearch,
    vaultSearchCoverage,
    VAULT_SEARCH_FAILURE,
} from "../../../src/features/chat/vault-search-view.ts";
import { renderHistoricalEvent, renderToolResult } from "./render-tool-result.ts";

const scope = { workspaceId: "a".repeat(32), taskId: "b".repeat(32) };
const hit = {
    source: {
        workspace_id: scope.workspaceId,
        relative_path: "notes/😀笔记.md",
        sha256: "c".repeat(64),
        start_line: 2,
        end_line: 2,
    },
    column_number: 2,
    snippet: '😀Docker <img src=x onerror="window.injected=true">',
    snippet_start_column: 1,
    snippet_truncated: false,
};
const base = {
    source: "authorized_vault", content_trust: "untrusted",
    workspace_id: scope.workspaceId, task_id: scope.taskId,
    query: "Docker", matches: [hit], searched_files: 1, read_bytes: 100,
    truncated: false, incomplete_reasons: [] as string[],
};
const parse = (change: Record<string, unknown> = {}) => parseVaultSearch("search_vault", JSON.stringify({ ...base, ...change }), scope);
const render = (change: Record<string, unknown> = {}) => renderToolResult("search_vault", JSON.stringify({ ...base, ...change }), scope);

test("结果绑定当前任务，按Unicode码点保留定位和不可信正文", () => {
    const value = parse();
    assert.ok(value);
    assert.equal(value.matches[0].columnNumber, 2);
    assert.equal(value.matches[0].snippet, hit.snippet);
    assert.equal(value.matches[0].relativePath, hit.source.relative_path);
    assert.equal(parseVaultSearch("search_vault", JSON.stringify(base)), null);
    assert.equal(parseVaultSearch("search_vault", JSON.stringify(base), { ...scope, taskId: "d".repeat(32) }), null);
    assert.equal(parseVaultSearch("search_vault", JSON.stringify(base), { ...scope, workspaceId: "d".repeat(32) }), null);
    assert.equal(parseVaultSearch("read_text_file", JSON.stringify(base), scope), null);
});

for (const [name, change] of Object.entries({
    source: { source: "project_files" }, trust: { content_trust: "trusted" },
    workspace: { workspace_id: "d".repeat(32) }, task: { task_id: "d".repeat(32) },
    missingQuery: { query: null }, emptyQuery: { query: "" }, longQuery: { query: "x".repeat(129) },
    queryLF: { query: "a\nb" }, queryCR: { query: "a\rb" }, queryNUL: { query: "a\x00b" },
    surrogate: { query: "\ud800" }, files: { searched_files: 21 }, fraction: { searched_files: 0.5 },
    noFiles: { searched_files: 0 }, bytes: { read_bytes: 20 * 256 * 1024 + 1 },
    negativeBytes: { read_bytes: -1 }, unsafeInteger: { read_bytes: Number.MAX_SAFE_INTEGER + 1 },
    reasonMismatch: { truncated: true }, unknownReason: { truncated: true, incomplete_reasons: ["PRIVATE"] },
    duplicateReason: { truncated: true, incomplete_reasons: ["inventory_truncated", "inventory_truncated"] },
    contradictoryFiles: { truncated: true, incomplete_reasons: ["file_budget"] },
    contradictoryMatches: { truncated: true, incomplete_reasons: ["match_budget"] },
    matchArray: { matches: {} }, tooMany: { matches: Array(51).fill(hit) },
    extra: { private: "PRIVATE" },
})) {
    test(`拒绝结果协议 ${name}`, () => assert.equal(parse(change), null));
}

for (const [name, change] of Object.entries({
    column: { column_number: 3 }, zeroColumn: { column_number: 0 },
    start: { snippet_start_column: 0 }, negativeOffset: { snippet_start_column: 3 },
    falseCrop: { snippet_start_column: 2, column_number: 3 },
    newline: { snippet: "😀Docker\n" }, nul: { snippet: "😀Docker\x00" },
    surrogate: { snippet: "😀Docker\ud800" }, long: { snippet: "😀Docker" + "x".repeat(200) },
    unmatched: { snippet: "docker" }, flag: { snippet_truncated: "false" }, extra: { private: "PRIVATE" },
})) {
    test(`拒绝片段协议 ${name}`, () => assert.equal(parse({ matches: [{ ...hit, ...change }] }), null));
}

for (const [name, change] of Object.entries({
    workspace: { workspace_id: "d".repeat(32) }, digest: { sha256: "Z".repeat(64) },
    zeroLine: { start_line: 0, end_line: 0 }, range: { end_line: 3 },
    extra: { root_path: "PRIVATE" },
    ...Object.fromEntries([
        "/note.md", "../note.md", "a/../note.md", "./note.md", "a//note.md",
        "C:/note.md", "a\\note.md", ".obsidian/note.md", "a/.hidden.md",
        "notes.txt", "a/CON.md", "a./note.md", "a /note.md", "a\x00.md",
    ].map((path, index) => [`path${index}`, { relative_path: path }])),
})) {
    test(`拒绝来源协议 ${name}`, () => assert.equal(parse({ matches: [{ ...hit, source: { ...hit.source, ...change } }] }), null));
}

test("坏JSON、空对象、缺字段、重复行和同篇摘要不一致", () => {
    for (const raw of ["null", "[]", "{", "{}", JSON.stringify({ ...base, matches: null })]) {
        assert.equal(parseVaultSearch("search_vault", raw, scope), null);
    }
    const { read_bytes: removed, ...missing } = base;
    void removed;
    assert.equal(parseVaultSearch("search_vault", JSON.stringify(missing), scope), null);
    assert.equal(parse({ matches: [hit, hit] }), null);
    assert.equal(parse({ matches: [hit, { ...hit, source: { ...hit.source, start_line: 3, end_line: 3, sha256: "d".repeat(64) } }] }), null);
    assert.equal(parse({ matches: [hit, { ...hit, source: { ...hit.source, relative_path: "other.md" } }] }), null);
});

test("完整无匹配、覆盖不完整及片段裁剪分离", () => {
    const empty = parse({ matches: [], searched_files: 0, read_bytes: 0 });
    assert.ok(empty);
    assert.equal(vaultSearchCoverage(empty), "本次检索范围内没有匹配。");
    const incomplete = parse({ matches: [], searched_files: 20, truncated: true, incomplete_reasons: ["file_budget"] });
    assert.ok(incomplete);
    assert.match(vaultSearchCoverage(incomplete), /仍有内容未完整检查/);
    const cropped = parse({ matches: [{ ...hit, snippet_truncated: true }] });
    assert.ok(cropped);
    assert.equal(cropped.truncated, false);
    assert.equal(cropped.matches[0].snippetTruncated, true);
    assert.ok(parse({ truncated: true, incomplete_reasons: ["inventory_truncated"] }));
    const matches = Array.from({ length: 50 }, (_, index) => ({ ...hit, source: { ...hit.source, start_line: index + 1, end_line: index + 1 } }));
    assert.ok(parse({ matches, truncated: true, incomplete_reasons: ["match_budget"] }));
});

test("UTF8完整JSON预算和查询字符预算", () => {
    const raw = JSON.stringify(base);
    const bytes = Buffer.byteLength(raw);
    assert.ok(parseVaultSearch("search_vault", raw + " ".repeat(64 * 1024 - bytes), scope));
    assert.equal(parseVaultSearch("search_vault", raw + " ".repeat(64 * 1024 - bytes + 1), scope), null);
    const query = "😀".repeat(128);
    assert.ok(parse({ query, matches: [], searched_files: 0, read_bytes: 0 }));
    assert.equal(parse({ query: query + "😀", matches: [] }), null);
});

test("当前卡片转义资料、展示来源和折叠摘要，不生成链接或执行入口", () => {
    const html = render();
    assert.match(html, /Vault 笔记检索/);
    assert.match(html, /notes\/😀笔记.md:2/);
    assert.match(html, /命中第 2 列/);
    assert.match(html, /本次文件 SHA-256/);
    assert.match(html, /查看历史不会重新检索/);
    assert.match(html, /&lt;img/);
    assert.doesNotMatch(html, /<img|<script|<a\b|<button/);
    const cropped = render({ matches: [{ ...hit, snippet_truncated: true }] });
    assert.match(cropped, /片段已裁剪/);
    assert.doesNotMatch(cropped, /检索不完整/);
});

test("空结果与不完整文案、错误协议不退回原始文本", () => {
    assert.match(render({ matches: [] }), /本次检索范围内没有匹配/);
    const incomplete = render({ matches: [], searched_files: 20, truncated: true, incomplete_reasons: ["file_budget"] });
    assert.match(incomplete, /仍有内容未完整检查/);
    assert.match(incomplete, /20 个文件的检索上限/);
    const bad = render({ private: "PRIVATE" });
    assert.match(bad, /无法确认，未显示笔记片段/);
    assert.doesNotMatch(bad, /PRIVATE|<pre|notes\/😀笔记/);
});

test("历史共用当前任务解析和卡片，失败不当空匹配", () => {
    const event = { event_type: "TOOL_CALL_RESULT", payload: { tool_name: "search_vault", result: JSON.stringify(base) } };
    assert.match(renderHistoricalEvent(event, scope), /Vault 笔记检索/);
    assert.match(renderHistoricalEvent(event, { ...scope, taskId: "d".repeat(32) }), /无法确认/);
    const error = renderHistoricalEvent({ event_type: "TOOL_CALL_ERROR", payload: { tool_name: "search_vault", message: "暂时无法读取" } }, scope);
    assert.ok(error.includes(VAULT_SEARCH_FAILURE));
    assert.doesNotMatch(error, /本次检索范围内没有匹配|Vault 笔记检索/);
});
