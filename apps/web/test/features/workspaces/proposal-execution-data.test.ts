import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { readProposalExecutionReceipt } from "../../../src/features/workbench/proposal-execution-data.ts";

const ids = ["a".repeat(32), "b".repeat(32), "c".repeat(32)] as const;
const prefix = "proposal_application_";
function payload(): Record<string, unknown> {
    return {
        workspace_id: ids[0], task_id: ids[1], proposal_id: ids[2],
        file_status: "replaced", application_status: "applied",
        code: prefix + "applied", cleanup_complete: true,
    };
}
function read(raw: unknown) {
    return readProposalExecutionReceipt(raw, ...ids);
}

test("240 combinations agree with backend; 17 accepted", () => {
    const cases: Record<string, unknown>[] = [];
    for (const file of ["not_attempted", "not_replaced", "replaced", "uncertain"]) {
        for (const clean of [null, true, false]) {
            for (const status of ["applied", "not_applied", "uncertain", "unknown"]) {
                for (const code of ["applied", "not_applied", "uncertain", "claim_unconfirmed", "registration_unconfirmed"]) {
                    cases.push({ ...payload(), file_status: file, cleanup_complete: clean,
                        application_status: status, code: prefix + code });
                }
            }
        }
    }
    // 只加载真实Schema对照协议，不访问数据库或执行器。
    const python = fileURLToPath(new URL("../../../../../.venv/bin/python", import.meta.url));
    const cwd = fileURLToPath(new URL("../../../../api/", import.meta.url));
    const script = `
import json, sys
from pydantic import ValidationError
from app.schemas import FileEditProposalExecutionResponse
results = []
for item in json.load(sys.stdin):
    try:
        FileEditProposalExecutionResponse.model_validate(item)
        results.append(True)
    except ValidationError:
        results.append(False)
print(json.dumps(results))
`;
    const accepted: boolean[] = JSON.parse(execFileSync(python, ["-c", script], {
        cwd, input: JSON.stringify(cases), encoding: "utf8", timeout: 10000,
    }));
    assert.equal(cases.length, 240);
    assert.equal(accepted.length, 240);
    assert.equal(accepted.filter(Boolean).length, 17);
    cases.forEach((item, index) => {
        const parsed = read(item);
        assert.equal(parsed !== null, accepted[index], JSON.stringify(item));
        if (parsed) {
            assert.deepEqual(parsed, item);
            assert.deepEqual(read(JSON.parse(JSON.stringify(parsed))), parsed);
        }
    });
});

for (const raw of [null, undefined, true, 1, "text", [], [payload()]]) {
    test(`reject non-record ${JSON.stringify(raw)}`, () => assert.equal(read(raw), null));
}
for (const key of Object.keys(payload())) {
    test(`require own field ${key}`, () => {
        const missing = payload();
        delete missing[key];
        assert.equal(read(missing), null);
        const inherited = Object.assign(Object.create({ [key]: payload()[key] }), missing);
        assert.equal(read(inherited), null);
    });
}
for (const [field, value] of [
    ["cleanup_complete", 0], ["cleanup_complete", 1], ["cleanup_complete", "true"],
    ["cleanup_complete", "false"], ["cleanup_complete", undefined], ["cleanup_complete", {}],
    ["file_status", "PRIVATE"], ["file_status", null],
    ["application_status", "idle"], ["application_status", "running"],
    ["application_status", undefined], ["code", "PRIVATE"], ["code", null],
] as const) {
    test(`reject ${field}=${JSON.stringify(value)}`, () => {
        assert.equal(read({ ...payload(), [field]: value }), null);
    });
}
for (const [index, key] of ["workspace_id", "task_id", "proposal_id"].entries()) {
    test(`reject mismatched ${key}`, () => {
        assert.equal(read({ ...payload(), [key]: "d".repeat(32) }), null);
    });
    for (const invalid of ["", "A".repeat(32), "g".repeat(32), "a".repeat(31), "a".repeat(33), "a".repeat(31) + "\n"]) {
        test(`reject malformed ${key}=${JSON.stringify(invalid)}`, () => {
            const scope: [string, string, string] = [...ids];
            scope[index] = invalid;
            assert.equal(readProposalExecutionReceipt({ ...payload(), [key]: invalid }, ...scope), null);
        });
    }
}

test("explicit projection preserves frozen input and drops private fields", () => {
    const raw = Object.freeze({ ...payload(), application_token: "PRIVATE-token",
        bound_root: "/PRIVATE/path", proposed_content: "PRIVATE-content" });
    const before = JSON.stringify(raw);
    const result = read(raw);
    assert.deepEqual(result, payload());
    assert.notEqual(result, raw);
    assert.equal(JSON.stringify(raw), before);
    assert.equal(JSON.stringify(result).includes("PRIVATE"), false);
    const extra = Object.defineProperty(payload(), "debug", {
        enumerable: true, get() { throw new Error("must not read private field"); },
    });
    assert.deepEqual(read(extra), payload());
});

test("replaced plus unknown is preserved; no fetch or retry", (t) => {
    const fetch = t.mock.method(globalThis, "fetch", () => {
        throw new Error("parser must not perform network I/O");
    });
    const raw = { ...payload(), application_status: "unknown",
        code: prefix + "registration_unconfirmed" };
    assert.deepEqual(read(raw), raw);
    assert.equal(read({ ...raw, code: prefix + "claim_unconfirmed" }), null);
    assert.equal(fetch.mock.callCount(), 0);
});
