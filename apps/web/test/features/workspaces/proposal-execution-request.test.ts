import assert from "node:assert/strict";
import { test } from "node:test";
import { createProposalExecutionRequest } from "../../../src/features/workbench/proposal-execution-request.ts";

const scope = { workspaceId: "a".repeat(32), taskId: "b".repeat(32), proposalId: "c".repeat(32) };
const other = { ...scope, proposalId: "d".repeat(32) };
const receipt = (target = scope) => ({
    workspace_id: target.workspaceId, task_id: target.taskId, proposal_id: target.proposalId,
    file_status: "replaced", application_status: "applied", code: "proposal_application_applied", cleanup_complete: true,
});
function storage() {
    const entries = new Map<string, string>();
    return { entries, getItem: (key: string) => entries.get(key) ?? null, setItem: (key: string, value: string) => { entries.set(key, value); } };
}
function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (reason: unknown) => void;
    const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

test("persists guard before fetch, exact request, duplicate lock and restored receipt", async () => {
    const store = storage();
    const pending = deferred<Response>();
    let calls = 0;
    const model = createProposalExecutionRequest({ storage: store, fetch: async (url, init) => {
        calls += 1;
        assert.equal([...store.entries.values()][0], "uncertain");
        assert.equal(url, `/api/workspaces/${scope.workspaceId}/tasks/${scope.taskId}/file-edit-proposals/${scope.proposalId}/apply`);
        assert.equal(init?.body, '{"action":"apply"}');
        assert.equal(init?.method, "POST");
        assert.equal(init?.cache, "no-store");
        assert.equal(init?.redirect, "error");
        assert.deepEqual(init?.headers, { "Content-Type": "application/json" });
        return pending.promise;
    } });
    model.select(scope);
    const first = model.submit();
    assert.equal(model.getState().phase, "submitting");
    await model.submit();
    pending.resolve(Response.json({ ...receipt(), private: "PRIVATE" }));
    await first;
    assert.equal(model.getState().phase, "receipt");
    assert.ok(!JSON.stringify(model.getState()).includes("PRIVATE"));
    await model.submit();
    const restored = createProposalExecutionRequest({ storage: store, fetch: async () => { throw Error("must not send"); } });
    restored.select(scope);
    assert.deepEqual(restored.getState(), model.getState());
    await restored.submit();
    assert.equal(calls, 1);
});

for (const kind of ["network", "malformed", "wrong-resource", "invalid-receipt", "409", "500", "302"]) {
    test(`${kind} preserves guard and never retries`, async () => {
        const store = storage();
        let calls = 0;
        const model = createProposalExecutionRequest({ storage: store, fetch: async () => {
            calls++;
            if (kind === "network") throw Error("PRIVATE");
            if (kind === "malformed") return new Response("PRIVATE{");
            if (kind === "wrong-resource") return Response.json(receipt(other));
            if (kind === "invalid-receipt") return Response.json({ ...receipt(), cleanup_complete: null });
            return Response.json({ code: "PRIVATE" }, { status: Number(kind) });
        } });
        model.select(scope);
        await model.submit();
        assert.equal(model.getState().phase, "uncertain");
        assert.equal([...store.entries.values()][0], "uncertain");
        assert.ok(!JSON.stringify(model.getState()).includes("PRIVATE"));
        await model.submit();
        model.select(null); model.select(scope); await model.submit();
        assert.equal(calls, 1);
    });
}
for (const kind of ["cancel", "switch", "dispose", "timeout"]) {
    test(`${kind} isolates late success including switch away and back`, async () => {
        const store = storage();
        const pending = deferred<Response>();
        const timeout = new AbortController();
        let signal: AbortSignal | null | undefined;
        const model = createProposalExecutionRequest({ storage: store, timeout: ms => {
            assert.equal(ms, 25_000); return timeout.signal;
        }, fetch: async (_url, init) => { signal = init?.signal; return pending.promise; } });
        model.select(scope);
        const task = model.submit();
        if (kind === "cancel") model.cancel();
        if (kind === "switch") { model.select(other); model.select(scope); }
        if (kind === "dispose") model.dispose();
        if (kind === "timeout") timeout.abort();
        assert.equal(signal?.aborted, true);
        pending.resolve(Response.json(receipt()));
        await task;
        assert.equal(model.getState().phase, "uncertain");
        assert.equal([...store.entries.values()][0], "uncertain");
    });
}
for (const stage of ["read", "write", "receipt", "corrupt"]) {
    test(`storage ${stage} fails closed`, async () => {
        const store = storage();
        let calls = 0;
        let writes = 0;
        const model = createProposalExecutionRequest({ storage: {
            getItem: key => {
                if (stage === "read") throw Error("PRIVATE");
                if (stage === "corrupt") return "PRIVATE{";
                return store.getItem(key);
            },
            setItem: (key, value) => {
                writes++;
                if (stage === "write" || (stage === "receipt" && writes === 2)) throw Error("PRIVATE");
                store.setItem(key, value);
            },
        }, fetch: async () => { calls++; return Response.json(receipt()); } });
        model.select(scope); await model.submit(); await model.submit();
        assert.equal(calls, stage === "receipt" ? 1 : 0);
        assert.equal(model.getState().phase, stage === "receipt" ? "receipt" : "unavailable");
        if (stage === "receipt") assert.equal([...store.entries.values()][0], "uncertain");
    });
}
test("shared storage blocks a second controller with stale idle state", async () => {
    const store = storage();
    let calls = 0;
    const deps = { storage: store, fetch: async () => { calls++; return Response.json(receipt()); } };
    const a = createProposalExecutionRequest(deps), b = createProposalExecutionRequest(deps);
    a.select(scope); b.select(scope);
    await Promise.all([a.submit(), b.submit()]);
    assert.equal(calls, 1);
    assert.notEqual(b.getState().phase, "idle");
});
test("scope snapshot, invalid identifiers and disposed instance", async () => {
    let calls = 0;
    const model = createProposalExecutionRequest({ storage: storage(), fetch: async () => { calls++; return Response.json(receipt()); } });
    for (const field of Object.keys(scope)) assert.throws(() => model.select({ ...scope, [field]: "bad" }));
    const input = { ...scope }; model.select(input); input.proposalId = other.proposalId;
    assert.equal(model.getState().scope?.proposalId, scope.proposalId);
    assert.ok(Object.isFrozen(model.getState()));
    assert.ok(Object.isFrozen(model.getState().scope));
    model.dispose(); model.select(other); await model.submit();
    assert.equal(calls, 0);
});
for (const kind of ["claim", "registration", "uncertain", "not-applied"]) {
    test(`valid ${kind} receipt preserved without unlocking`, async () => {
        const outcome = kind === "claim"
            ? { file_status: "not_attempted", application_status: "unknown", code: "proposal_application_claim_unconfirmed", cleanup_complete: null }
            : kind === "registration"
                ? { file_status: "replaced", application_status: "unknown", code: "proposal_application_registration_unconfirmed", cleanup_complete: true }
                : kind === "uncertain"
                    ? { file_status: "uncertain", application_status: "uncertain", code: "proposal_application_uncertain", cleanup_complete: null }
                    : { file_status: "not_replaced", application_status: "not_applied", code: "proposal_application_not_applied", cleanup_complete: true };
        const expected = { ...receipt(), ...outcome };
        let calls = 0;
        const model = createProposalExecutionRequest({ storage: storage(), fetch: async () => { calls++; return Response.json(expected); } });
        model.select(scope); await model.submit(); await model.submit();
        assert.deepEqual(model.getState(), { phase: "receipt", scope, receipt: expected });
        assert.equal(calls, 1);
    });
}
test("cancel while parsing body prevents late state and storage update", async () => {
    const pending = deferred<unknown>();
    const entered = deferred<void>();
    const response = Response.json(receipt());
    response.json = () => { entered.resolve(); return pending.promise; };
    const model = createProposalExecutionRequest({ storage: storage(), fetch: async () => response });
    model.select(scope); const run = model.submit(); await entered.promise;
    model.cancel(); pending.resolve(receipt()); await run;
    assert.equal(model.getState().phase, "uncertain");
});

test("old response cannot replace a new resource's receipt", async () => {
    const old = deferred<Response>();
    let calls = 0;
    const model = createProposalExecutionRequest({ storage: storage(), fetch: async () => {
        calls++;
        return calls === 1 ? old.promise : Response.json(receipt(other));
    } });
    model.select(scope); const first = model.submit();
    model.select(other); await model.submit();
    old.resolve(Response.json(receipt())); await first;
    assert.deepEqual(model.getState(), { phase: "receipt", scope: other, receipt: receipt(other) });
});

test("subscription disposal and observer failures do not alter execution", async () => {
    const model = createProposalExecutionRequest({ storage: storage(), fetch: async () => Response.json(receipt()) });
    const phases: string[] = [];
    model.subscribe(() => { throw Error("observer"); });
    const unsubscribe = model.subscribe(() => phases.push(model.getState().phase));
    model.select(scope); await model.submit();
    assert.deepEqual(phases, ["idle", "submitting", "receipt"]);
    unsubscribe(); model.select(null);
    assert.equal(phases.length, 3);
});
