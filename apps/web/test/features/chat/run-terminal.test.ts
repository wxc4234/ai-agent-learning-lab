import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

import { readAgentStream, type AgentRunMetrics } from "../../../src/features/chat/agent-stream.ts";
import {
    chatReducer,
    initialChatState,
    toUserFacingError,
    type ChatAction,
} from "../../../src/features/chat/chat-state.ts";

const metrics: AgentRunMetrics = {
    model_usage: null,
    model_duration_ms: null,
    tool_duration_ms: 0,
    estimated_cost_cny: null,
    pricing: {
        model: "deepseek-chat",
        tier: "peak",
        cache_hit_input_cny_per_million: "0.10",
        cache_miss_input_cny_per_million: "3.0",
        output_cny_per_million: "9.0",
    },
};
const summary = { stepsTaken: 0, metrics };
const failure = {
    type: "RUN_ERROR",
    code: "token_usage_unknown",
    message: "无法确定 Token 用量",
    steps_taken: 0,
    metrics,
};
const success = { type: "RUN_FINISHED", steps_taken: 0, metrics };
const ordinaryFailure = {
    type: "RUN_ERROR",
    code: "model_error",
    message: "模型服务暂时不可用",
};

// 执行组件中实际定义的请求与取消函数；不复制业务实现、不渲染 React。
// AST 只用于定位函数，事件解析、状态更新及异步控制流均执行生产代码。
const panelSource = readFileSync(
    new URL("../../../src/features/chat/components/chat-panel.tsx", import.meta.url),
    "utf8",
);
const sourceFile = ts.createSourceFile(
    "chat-panel.tsx", panelSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX,
);
function readFunction(name: string): string {
    let found: ts.FunctionDeclaration | undefined;
    function visit(node: ts.Node) {
        if (ts.isFunctionDeclaration(node) && node.name?.text === name) {
            found = node;
        }
        ts.forEachChild(node, visit);
    }
    visit(sourceFile);
    assert.ok(found, `组件必须包含 ${name}，重构时请同步调整测试入口`);
    return found.getText(sourceFile);
}
const requestCode = ts.transpileModule(
    `${readFunction("startRequest")}\n${readFunction("requestCancellation")}`,
    { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } },
).outputText;

function createHarness(fetchResponse: typeof fetch) {
    let state = initialChatState;
    let cancellationNotice: string | null = null;
    const actions: ChatAction[] = [];
    const timers = new Map<number, () => void>();
    let nextTimer = 0;
    const controllerRef = { current: null as AbortController | null };
    const cancellationPendingRef = { current: false };
    const dependencies = {
        fetch: fetchResponse,
        workbench: { setBusy: () => {} },
        setPrompt: () => {},
        setCreationError: () => {},
        chatState: initialChatState,
        setHistory: () => {},
        mountedRef: { current: true },
        summarizeTitle: async () => {},
        requestVersionRef: { current: 0 },
        setCancellationNotice(value: string | null) { cancellationNotice = value; },
        readAgentStream,
        toUserFacingError,
        dispatch(action: ChatAction) {
            actions.push(action);
            state = chatReducer(state, action);
        },
        controllerRef,
        cancellationPendingRef,
        activeRunIdRef: { current: null as string | null },
        sessionIdRef: { current: null as string | null },
        setActiveRunId: () => {},
        REQUEST_TIMEOUT_MS: 30_000,
        window: {
            setTimeout(callback: () => void) {
                const id = ++nextTimer;
                timers.set(id, callback);
                return id;
            },
            clearTimeout(id: number) { timers.delete(id); },
        },
    };
    const factory = new Function(
        ...Object.keys(dependencies),
        `${requestCode}\nreturn { startRequest, requestCancellation };`,
    );
    const methods = factory(...Object.values(dependencies)) as {
        startRequest(prompt: string, isRetry?: boolean): Promise<void>;
        requestCancellation(reason: "user" | "timeout"): Promise<void>;
    };
    return {
        ...methods,
        actions,
        timers,
        controllerRef,
        cancellationPendingRef,
        activeRunIdRef: dependencies.activeRunIdRef,
        requestVersionRef: dependencies.requestVersionRef,
        get cancellationNotice() { return cancellationNotice; },
        get state() { return state; },
    };
}

function responseWithLines(events: unknown[], trailingLine?: string) {
    const lines = events.map((event) => JSON.stringify(event));
    if (trailingLine !== undefined) lines.push(trailingLine);
    return new Response(lines.join("\n") + "\n", {
        headers: { "X-Run-ID": "run-test" },
    });
}

test("failure atomically stores zero steps and nullable metrics with the message", () => {
    const state = chatReducer(initialChatState, {
        type: "fail", message: failure.message, summary,
    });
    assert.equal(state.status, "error");
    assert.equal(state.errorMessage, failure.message);
    assert.deepEqual(state.runSummary, summary);
});

test("ordinary failure, submit, retry, abort and reset clear a failed summary", () => {
    const failed = chatReducer(initialChatState, {
        type: "fail", message: failure.message, summary,
    });
    const actions: ChatAction[] = [
        { type: "fail", message: "网络错误" },
        { type: "submit", prompt: "新问题" },
        { type: "retry" },
        { type: "abort" },
        { type: "reset" },
    ];
    for (const action of actions) {
        assert.equal(chatReducer(failed, action).runSummary, null, action.type);
    }
});

for (const terminal of [success, failure, ordinaryFailure]) {
    for (const suffix of ["eof", "late-events", "invalid-json"]) {
        test(`${terminal.type} ${"code" in terminal ? terminal.code : "success"} stops before ${suffix}`, async () => {
            const events: unknown[] = [terminal];
            if (suffix === "late-events") {
                events.push({ type: "TEXT_MESSAGE_CONTENT", chunk: "不应追加" }, success);
            }
            const response = responseWithLines(events, suffix === "invalid-json" ? "invalid" : undefined);
            const harness = createHarness(async () => response);
            await harness.startRequest("测试");

            assert.equal(harness.state.status, terminal.type === "RUN_FINISHED" ? "done" : "error");
            assert.equal(harness.state.errorMessage, "message" in terminal ? terminal.message : null);
            assert.deepEqual(harness.state.runSummary, terminal === ordinaryFailure ? null : summary);
            assert.equal(harness.state.reply, "");
            assert.equal(harness.actions.length, 2, "只分发 submit 和一个终态 action");
            assert.equal(harness.timers.size, 0);
            assert.equal(harness.controllerRef.current, null);
            assert.equal(response.body?.locked, false);
        });
    }
}

test("tool failure and text end are not run terminals", async () => {
    const harness = createHarness(async () => responseWithLines([
        { type: "TOOL_CALL_START", tool_call_id: "tool-1", tool_name: "missing", arguments: "{}" },
        { type: "TOOL_CALL_ERROR", tool_call_id: "tool-1", tool_name: "missing", code: "unknown_tool", message: "未注册", duration_ms: null },
        { type: "TEXT_MESSAGE_START" },
        { type: "TEXT_MESSAGE_CONTENT", chunk: "回答" },
        { type: "TEXT_MESSAGE_END" },
        success,
    ]));
    await harness.startRequest("测试");
    assert.equal(harness.state.status, "done");
    assert.equal(harness.state.reply, "回答");
    assert.equal(harness.state.tools[0]?.status, "failed");
    assert.deepEqual(harness.state.runSummary, summary);
});

for (const [label, fetchResponse, message] of [
    ["HTTP 503", async () => new Response(null, { status: 503 }), "执行服务暂时繁忙或不可用，请稍后再试。"],
    ["HTTP 409", async () => new Response(null, { status: 409 }), "该会话仍在执行或收尾，请稍后再试"],
    ["HTTP 429", async () => new Response(null, { status: 429 }), "请求太频繁了，请稍后再试。"],
    ["HTTP 502", async () => new Response(null, { status: 502 }), "模型服务暂时不可用，请稍后重试。"],
    ["network", async () => { throw new TypeError("offline"); }, "网络连接中断，请检查网络后重试。"],
    ["missing body", async () => new Response(null), "服务端没有返回流式内容"],
    ["premature EOF", async () => responseWithLines([{ type: "TEXT_MESSAGE_CONTENT", chunk: "半截" }]), "响应意外中断，请重试。"],
    ["malformed event", async () => responseWithLines([], "invalid"), "服务端返回了无效的 Agent 事件"],
] as const) {
    test(`${label} fails without inventing a summary`, async () => {
        const harness = createHarness(fetchResponse);
        await harness.startRequest("测试");
        assert.equal(harness.state.status, "error");
        assert.equal(harness.state.errorMessage, message);
        assert.equal(harness.state.runSummary, null);
        assert.equal(harness.timers.size, 0);
    });
}

for (const reason of ["user", "timeout"] as const) {
    test(`${reason} wins when cancellation is pending at a terminal event`, async () => {
        let releaseResponse!: (response: Response) => void;
        const pending = new Promise<Response>((resolve) => { releaseResponse = resolve; });
        const harness = createHarness(async () => pending);
        const running = harness.startRequest("测试");
        if (reason === "timeout") {
            for (const callback of harness.timers.values()) callback();
        } else {
            await harness.requestCancellation("user");
        }
        releaseResponse(responseWithLines([failure]));
        await running;
        assert.equal(harness.state.status, reason === "user" ? "aborted" : "error");
        assert.equal(harness.state.errorMessage, reason === "user" ? null : "请求超时了，请稍后重试。");
        assert.equal(harness.state.runSummary, null);
    });
}

test("a delayed cancellation only aborts its captured controller", async () => {
    let releaseCancellation!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => { releaseCancellation = resolve; });
    const harness = createHarness(async () => pending);
    const oldController = new AbortController();
    const newController = new AbortController();
    harness.controllerRef.current = oldController;
    harness.activeRunIdRef.current = "old-run";
    const cancelling = harness.requestCancellation("user");
    harness.controllerRef.current = newController;
    releaseCancellation(new Response(null));
    await cancelling;
    assert.equal(oldController.signal.aborted, true);
    assert.equal(newController.signal.aborted, false);
});

for (const [status, message] of [
    [204, null],
    [401, "登录状态已失效"],
    [404, "运行不存在或不可访问"],
    [503, "取消服务暂时不可用"],
] as const) {
    test(`cancellation ${status} preserves classification and aborts local stream`, async () => {
        const controller = new AbortController();
        const harness = createHarness(async (_url, init) => {
            assert.notEqual(init?.signal, controller.signal);
            assert.deepEqual(JSON.parse(init?.body as string), {reason:"user"});
            return new Response(null, {status});
        });
        harness.controllerRef.current = controller;
        harness.activeRunIdRef.current = "17";
        await harness.requestCancellation("user");
        assert.equal(controller.signal.aborted, true);
        assert.equal(harness.timers.size, 0);
        if (message === null) assert.equal(harness.cancellationNotice, null);
        else assert.ok(harness.cancellationNotice?.includes(message));
    });
}

test("cancellation timeout aborts its own fetch and then local stream", async () => {
    const controller = new AbortController();
    const harness = createHarness(async (_url, init) => new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    harness.controllerRef.current = controller;
    harness.activeRunIdRef.current = "17";
    const pending = harness.requestCancellation("user");
    for (const timer of [...harness.timers.values()]) timer();
    await pending;
    assert.equal(controller.signal.aborted, true);
    assert.match(harness.cancellationNotice!, /取消请求未完成/);
    assert.equal(harness.timers.size, 0);
});

test("duplicate cancellation sends one request and stale response cannot write notice", async () => {
    let resolve!: (response: Response) => void;
    let calls = 0;
    const controller = new AbortController();
    const harness = createHarness(async () => {
        calls += 1;
        return new Promise<Response>(done => { resolve = done; });
    });
    harness.controllerRef.current = controller;
    harness.activeRunIdRef.current = "17";
    const first = harness.requestCancellation("user");
    await harness.requestCancellation("timeout");
    assert.equal(calls, 1);
    harness.requestVersionRef.current += 1;
    resolve(new Response(null, {status:503}));
    await first;
    assert.equal(harness.cancellationNotice, null);
    assert.equal(controller.signal.aborted, true);
});

test("no active request causes no cancellation fetch", async () => {
    const harness = createHarness(async () => { throw new Error("must not fetch"); });
    await harness.requestCancellation("user");
    assert.equal(harness.cancellationPendingRef.current, false);
    assert.equal(harness.timers.size, 0);
});
