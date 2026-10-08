// 本课独立JSON边界，不改变既有代理。限制实际字节、嵌套和重复键。
export function parseCodeContextJson(text: string): unknown {
    let cursor = 0;
    function space() {
        while (/[ \t\r\n]/.test(text[cursor] ?? "!") && cursor < text.length) cursor++;
    }
    function fail(): never { throw new Error("invalid_code_context_json"); }
    function string(): string {
        const token = /"(?:[^"\\\u0000-\u001f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"/y;
        token.lastIndex = cursor;
        const match = token.exec(text);
        if (!match) return fail();
        cursor = token.lastIndex;
        const value: string = JSON.parse(match[0]);
        for (const char of value) {
            const code = char.codePointAt(0)!;
            if (code >= 0xd800 && code <= 0xdfff) return fail();
        }
        return value;
    }
    function read(depth: number): unknown {
        if (depth > 16) return fail();
        space();
        const char = text[cursor];
        if (char === '"') return string();
        if (char === "{" || char === "[") {
            cursor++;
            const object: Record<string, unknown> = Object.create(null);
            const array: unknown[] = [];
            const close = char === "{" ? "}" : "]";
            space();
            if (text[cursor] === close) { cursor++; return char === "{" ? object : array; }
            while (true) {
                space();
                if (char === "{") {
                    const key = string();
                    if (Object.hasOwn(object, key)) return fail();
                    space();
                    if (text[cursor++] !== ":") return fail();
                    object[key] = read(depth + 1);
                } else array.push(read(depth + 1));
                space();
                if (text[cursor] === close) { cursor++; return char === "{" ? object : array; }
                if (text[cursor++] !== ",") return fail();
            }
        }
        const token = /(?:true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)/y;
        token.lastIndex = cursor;
        const match = token.exec(text);
        if (!match) return fail();
        cursor = token.lastIndex;
        const value: unknown = JSON.parse(match[0]);
        if (typeof value === "number" && !Number.isFinite(value)) return fail();
        if (typeof value === "number" && Number.isInteger(value)) {
            // 小数舍入成整数也不能冒充计数；1.0、100e-2等数学整数仍合法。
            const [coefficient, exponent = "0"] = match[0].replace(/^-/, "").split(/[eE]/);
            const [whole, fraction = ""] = coefficient.split(".");
            const digits = (whole + fraction).replace(/^0+/, "");
            const trailing = digits.length - digits.replace(/0+$/, "").length;
            if (digits && Number(exponent) < fraction.length - trailing) return fail();
        }
        return value;
    }
    const result = read(0);
    space();
    if (cursor !== text.length) return fail();
    return result;
}


// 公开代码上下文契约；只检查有界快照，不读取文件或授予后续访问权。
export type CodeQueryContextRequest = { query: string; batch_id: string; response_model: string };
type SourceFile = { relative_path: string; file_type: "source"; language: "python"; byte_count: number; sha256: string };
type Symbol = {
    relative_path: string; name: string; qualified_name: string;
    kind: "function" | "async_function" | "class";
    start_line: number; definition_line: number; end_line: number; sha256: string;
};
type Chunk = {
    chunk_id: string; symbol: Symbol; text: string; text_sha256: string;
    start_line: number; start_column: number; end_line: number; end_column: number;
    line_count: number; part_index: number; part_count: number; split_reasons: string[];
};
type Hit = { rank: number; distance: number; chunk: Chunk };
type Omission = { source_rank: number; chunk_id: string; reasons: string[] };
type Metadata = {
    workspace_id: string; task_id: string; files: SourceFile[];
    requested_model: string; response_model: string; dimensions: number; embedding_space_id: string;
    request_count: number; prompt_tokens: number | null; total_tokens: number | null;
    truncated: boolean; incomplete_reasons: string[];
    chunk_strategy: "python_innermost_definitions_v1"; chunk_policy: "gitignore_subset_v1";
    chunk_parser: string; source: "python_code_embeddings"; content_trust: "untrusted_project_content";
};
type Recall = { batch_chunk_count: number; searchable_chunk_count: number; excluded_zero_chunk_count: number; omitted_by_top_k: number };
export type CodeQueryContext = {
    query_sha256: string; requested_model: string; response_model: string;
    prompt_tokens: number | null; total_tokens: number | null; request_count: 1;
    query_source: "query_embedding"; source: "code_query_context";
    context: {
        batch_id: string; space_id: string; dimensions: number; input_hit_count: number; selected_hit_count: number;
        context_text: string; context_chars: number; context_bytes: number;
        budget: { max_chunks: number; max_chars: number; max_bytes: number };
        selected_chunks: Hit[]; omissions: Omission[]; source_metadata: Metadata; recall_summary: Recall;
        source: "bounded_code_context"; content_trust: "untrusted_project_content";
    };
};

const strategy = "python_innermost_definitions_v1";
const trust = "untrusted_project_content";
const splitReasons = ["nested_definition", "line_budget", "character_budget", "byte_budget"];
const coverageReasons = ["directory_entries", "directory_budget", "depth_budget", "file_budget", "unsupported_path", "chunk_budget"];
const omissionReasons = ["duplicate_chunk", "chunk_budget", "character_budget", "byte_budget"];
const identifier = new RegExp("^[\\p{XID_Start}_][\\p{XID_Continue}]*$", "u");
const encoder = new TextEncoder();
const pythonWhitespace = new RegExp("^[\\p{White_Space}\\u001c-\\u001f]+|[\\p{White_Space}\\u001c-\\u001f]+$", "gu");

function strip(value: string): string { return value.replace(pythonWhitespace, ""); }

function record(value: unknown): value is Record<string, unknown> {
    return value !== null && typeof value === "object" && !Array.isArray(value);
}
function exact(value: unknown, fields: string): value is Record<string, unknown> {
    const keys = fields.split(" ");
    return record(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
}
function integer(value: unknown, minimum: number, maximum: number): value is number {
    return typeof value === "number" && Number.isSafeInteger(value) && value >= minimum && value <= maximum;
}
function text(value: unknown, maximumChars: number, maximumBytes: number): value is string {
    return typeof value === "string" && [...value].length <= maximumChars && encoder.encode(value).length <= maximumBytes
        && [...value].every(char => { const code = char.codePointAt(0)!; return code < 0xd800 || code > 0xdfff; });
}
function label(value: unknown, maximum = 256): value is string {
    return text(value, maximum, 1024) && value.length > 0 && value === strip(value) && !/[\u0000-\u001f\u007f]/.test(value);
}
export function isCodeContextIdentifier(value: unknown): value is string {
    return typeof value === "string" && value.length === 32 && /^[a-f0-9]+$/.test(value);
}
function digest(value: unknown): value is string {
    return typeof value === "string" && value.length === 64 && /^[a-f0-9]+$/.test(value);
}
function reasons(value: unknown, allowed: string[], maximum: number): value is string[] {
    return Array.isArray(value) && value.length <= maximum && value.every(item => typeof item === "string" && allowed.includes(item))
        && new Set(value).size === value.length;
}
function usage(prompt: unknown, total: unknown): boolean {
    // JS不能精确表示任意int64；超出安全整数范围整次拒绝，不舍入或算作零。
    return (prompt === null && total === null)
        || (integer(prompt, 0, Number.MAX_SAFE_INTEGER) && integer(total, prompt, Number.MAX_SAFE_INTEGER));
}
function relativePath(value: unknown): value is string {
    if (!text(value, 4096, 4096) || !value || /[\\<>:"|?*\u0000-\u001f\u007f]/.test(value)) return false;
    return value.split("/").every(part => !!part && part !== "." && part !== ".." && !/[ .]$/.test(part)
        && !/^(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)/i.test(part));
}

export function readCodeQueryContextRequest(raw: unknown): CodeQueryContextRequest | null {
    if (!exact(raw, "query batch_id response_model") || !text(raw.query, 2000, 4096)
        || !strip(raw.query) || raw.query.includes("\u0000") || !isCodeContextIdentifier(raw.batch_id)
        || !label(raw.response_model)) return null;
    return { query: raw.query, batch_id: raw.batch_id, response_model: raw.response_model };
}

export async function codeContextDigest(value: string): Promise<string> {
    const result = await crypto.subtle.digest("SHA-256", encoder.encode(value));
    return Array.from(new Uint8Array(result), byte => byte.toString(16).padStart(2, "0")).join("");
}

function validMetadata(raw: unknown, workspaceId: string, taskId: string): boolean {
    if (!exact(raw, "workspace_id task_id files requested_model response_model dimensions embedding_space_id request_count prompt_tokens total_tokens truncated incomplete_reasons chunk_strategy chunk_policy chunk_parser source content_trust")
        || raw.workspace_id !== workspaceId || raw.task_id !== taskId || !label(raw.requested_model) || !label(raw.response_model)
        || !integer(raw.dimensions, 1, 4096) || !digest(raw.embedding_space_id) || !integer(raw.request_count, 1, 5)
        || !usage(raw.prompt_tokens, raw.total_tokens) || typeof raw.truncated !== "boolean"
        || !reasons(raw.incomplete_reasons, coverageReasons, 6) || raw.truncated !== (raw.incomplete_reasons.length > 0)
        || raw.chunk_strategy !== strategy || raw.chunk_policy !== "gitignore_subset_v1" || !label(raw.chunk_parser, 100)
        || raw.source !== "python_code_embeddings" || raw.content_trust !== trust
        || !Array.isArray(raw.files) || raw.files.length < 1 || raw.files.length > 20) return false;
    const seen = new Set<string>();
    for (const file of raw.files) {
        if (!exact(file, "relative_path file_type language byte_count sha256") || !relativePath(file.relative_path)
            || file.file_type !== "source" || file.language !== "python" || !integer(file.byte_count, 0, 65536)
            || !digest(file.sha256) || seen.has(file.relative_path)) return false;
        seen.add(file.relative_path);
    }
    return true;
}

async function validHit(raw: unknown, files: SourceFile[]): Promise<boolean> {
    if (!exact(raw, "rank distance chunk") || !integer(raw.rank, 1, 5) || typeof raw.distance !== "number"
        || !Number.isFinite(raw.distance) || raw.distance < 0 || raw.distance > 2
        || !exact(raw.chunk, "chunk_id symbol text start_line start_column end_line end_column line_count text_sha256 part_index part_count split_reasons")) return false;
    const chunk = raw.chunk;
    if (!digest(chunk.chunk_id) || !digest(chunk.text_sha256) || !text(chunk.text, 2000, 4096) || !chunk.text
        || /[\u0000\r]/.test(chunk.text) || !integer(chunk.line_count, 1, 40)
        || !integer(chunk.part_index, 1, 2 ** 31 - 1) || !integer(chunk.part_count, chunk.part_index, 2 ** 31 - 1)
        || !reasons(chunk.split_reasons, splitReasons, 4)
        || ![chunk.start_line, chunk.start_column, chunk.end_line, chunk.end_column].every(value => integer(value, 1, 2 ** 31 - 1))
        || !exact(chunk.symbol, "relative_path name qualified_name kind start_line definition_line end_line sha256")) return false;
    const symbol = chunk.symbol;
    if (!relativePath(symbol.relative_path) || !label(symbol.name, 1024) || !identifier.test(symbol.name)
        || !label(symbol.qualified_name, 1024) || !symbol.qualified_name.split(".").every(part => identifier.test(part))
        || symbol.qualified_name.split(".").at(-1) !== symbol.name || !["function", "async_function", "class"].includes(String(symbol.kind))
        || !digest(symbol.sha256) || !integer(symbol.start_line, 1, 2 ** 31 - 1)
        || !integer(symbol.definition_line, symbol.start_line, 2 ** 31 - 1) || !integer(symbol.end_line, symbol.definition_line, 2 ** 31 - 1)) return false;
    const c = chunk as unknown as Chunk;
    const s = c.symbol;
    const file = files.find(file => file.relative_path === s.relative_path);
    const lines = c.text.split("\n");
    const endColumn = lines.length > 1 ? [...lines.at(-1)!].length + 1 : c.start_column + [...c.text].length;
    if (!file || file.sha256 !== s.sha256 || encoder.encode(c.text).length > file.byte_count
        || c.start_line < s.start_line || c.end_line - Number(c.end_column === 1) > s.end_line
        || c.end_line !== c.start_line + lines.length - 1 || c.end_column !== endColumn
        || c.line_count !== lines.length - Number(c.text.endsWith("\n")) || await codeContextDigest(c.text) !== c.text_sha256) return false;
    const identity = [strategy, s.relative_path, s.sha256, s.qualified_name, s.kind, s.definition_line,
        c.start_line, c.start_column, c.end_line, c.end_column, c.text_sha256];
    return await codeContextDigest(JSON.stringify(identity)) === c.chunk_id;
}

function same(left: unknown, right: unknown): boolean {
    function canonical(value: unknown): unknown {
        if (Array.isArray(value)) return value.map(canonical);
        if (record(value)) return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
        return value;
    }
    return JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));
}

export async function readCodeQueryContext(
    raw: unknown, workspaceId: string, taskId: string, body: CodeQueryContextRequest,
): Promise<CodeQueryContext | null> {
    // 只有所有嵌套字段和交叉声明都确认后才投影；未知结果不能变成空上下文。
    try {
        if (![workspaceId, taskId].every(isCodeContextIdentifier)
            || !exact(raw, "query_sha256 requested_model response_model prompt_tokens total_tokens request_count context query_source source")
            || !digest(raw.query_sha256) || raw.query_sha256 !== await codeContextDigest(body.query)
            || !label(raw.requested_model) || raw.response_model !== body.response_model
            || !usage(raw.prompt_tokens, raw.total_tokens) || raw.request_count !== 1 || raw.query_source !== "query_embedding" || raw.source !== "code_query_context"
            || !exact(raw.context, "batch_id space_id dimensions input_hit_count selected_hit_count context_text context_chars context_bytes budget selected_chunks omissions source_metadata recall_summary source content_trust")) return null;
        const context = raw.context;
        if (context.batch_id !== body.batch_id || !digest(context.space_id) || !integer(context.dimensions, 1, 4096)
            || !integer(context.input_hit_count, 0, 5) || !integer(context.selected_hit_count, 0, context.input_hit_count)
            || !text(context.context_text, 12000, 24000) || context.context_chars !== [...context.context_text].length
            || context.context_bytes !== encoder.encode(context.context_text).length
            || !exact(context.budget, "max_chunks max_chars max_bytes") || context.budget.max_chunks !== 5
            || context.budget.max_chars !== 12000 || context.budget.max_bytes !== 24000
            || !Array.isArray(context.selected_chunks) || context.selected_chunks.length !== context.selected_hit_count
            || !Array.isArray(context.omissions) || context.omissions.length !== context.input_hit_count - context.selected_hit_count
            || !validMetadata(context.source_metadata, workspaceId, taskId)
            || !exact(context.recall_summary, "batch_chunk_count searchable_chunk_count excluded_zero_chunk_count omitted_by_top_k")
            || context.source !== "bounded_code_context" || context.content_trust !== trust) return null;
        const value = raw as unknown as CodeQueryContext;
        const c = value.context;
        const meta = c.source_metadata;
        const recall = c.recall_summary;
        if (meta.requested_model !== value.requested_model || meta.response_model !== value.response_model
            || meta.embedding_space_id !== c.space_id || meta.dimensions !== c.dimensions
            || !integer(recall.batch_chunk_count, 1, 20) || !integer(recall.searchable_chunk_count, 0, 20)
            || !integer(recall.excluded_zero_chunk_count, 0, 20) || !integer(recall.omitted_by_top_k, 0, 20)
            || recall.searchable_chunk_count + recall.excluded_zero_chunk_count !== recall.batch_chunk_count
            || c.input_hit_count !== Math.min(5, recall.searchable_chunk_count)
            || recall.omitted_by_top_k !== recall.searchable_chunk_count - c.input_hit_count) return null;
        let lastRank = 0, lastDistance = -1;
        for (const hit of c.selected_chunks) {
            if (!await validHit(hit, meta.files) || hit.rank <= lastRank || hit.distance < lastDistance) return null;
            lastRank = hit.rank; lastDistance = hit.distance;
        }
        lastRank = 0;
        for (const item of c.omissions) {
            if (!exact(item, "source_rank chunk_id reasons") || !integer(item.source_rank, 1, c.input_hit_count)
                || item.source_rank <= lastRank || !digest(item.chunk_id) || !reasons(item.reasons, omissionReasons, 4) || !item.reasons.length) return null;
            lastRank = item.source_rank;
        }
        const entries = [...c.selected_chunks.map(hit => ({ rank: hit.rank, id: hit.chunk.chunk_id, duplicate: false })),
            ...c.omissions.map(item => ({ rank: item.source_rank, id: item.chunk_id, duplicate: item.reasons.includes("duplicate_chunk") }))].sort((a, b) => a.rank - b.rank);
        const seen = new Set<string>();
        for (const [index, entry] of entries.entries()) {
            if (entry.rank !== index + 1 || entry.duplicate !== seen.has(entry.id)) return null;
            seen.add(entry.id);
        }
        const rendered = parseCodeContextJson(c.context_text);
        if (!exact(rendered, "scope batch_id embedding_space_id content_trust chunk_strategy chunk_policy snapshot_truncated snapshot_incomplete_reasons recall_omitted_by_top_k builder_omitted_hits chunks")
            || rendered.scope !== "selected_chunks_from_one_batch" || rendered.batch_id !== c.batch_id || rendered.embedding_space_id !== c.space_id
            || rendered.content_trust !== trust || rendered.chunk_strategy !== meta.chunk_strategy || rendered.chunk_policy !== meta.chunk_policy
            || rendered.snapshot_truncated !== meta.truncated || !same(rendered.snapshot_incomplete_reasons, meta.incomplete_reasons)
            || rendered.recall_omitted_by_top_k !== recall.omitted_by_top_k || rendered.builder_omitted_hits !== c.omissions.length
            || !same(rendered.chunks, c.selected_chunks)) return null;
        // 精确字段检查后创建独立公开副本；后续UI修改不能改写输入对象。
        return structuredClone({
            query_sha256: value.query_sha256, requested_model: value.requested_model, response_model: value.response_model,
            prompt_tokens: value.prompt_tokens, total_tokens: value.total_tokens, request_count: value.request_count,
            query_source: value.query_source, source: value.source,
            context: { batch_id: c.batch_id, space_id: c.space_id, dimensions: c.dimensions,
                input_hit_count: c.input_hit_count, selected_hit_count: c.selected_hit_count,
                context_text: c.context_text, context_chars: c.context_chars, context_bytes: c.context_bytes,
                budget: c.budget, selected_chunks: c.selected_chunks, omissions: c.omissions,
                source_metadata: c.source_metadata, recall_summary: c.recall_summary, source: c.source, content_trust: c.content_trust },
        });
    } catch { return null; }
}
