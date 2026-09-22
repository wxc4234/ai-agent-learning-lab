import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import { createElement } from "react";
import ts from "typescript";
import { createProposalExecutionRequest } from "../../../src/features/workbench/proposal-execution-request.ts";
import { readProposalExecutionReceipt } from "../../../src/features/workbench/proposal-execution-data.ts";

const require = createRequire(import.meta.url);
const { renderToStaticMarkup } = require("react-dom/server");
function compile(file: string, exports: string, dependencies: Record<string, unknown>) {
    const source = readFileSync(new URL(`../../../src/features/chat/components/${file}`, import.meta.url), "utf8");
    const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const body = ast.statements.filter(n => !ts.isImportDeclaration(n)).map(n => n.getText(ast)).join("\n").replace("export default function", "function");
    const code = ts.transpileModule(body, { compilerOptions: { target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React } }).outputText;
    return new Function("React", ...Object.keys(dependencies), `${code}\nreturn ${exports};`)( { createElement }, ...Object.values(dependencies));
}
const Result = compile("proposal-execution-result.tsx", "ProposalExecutionResultPanel", { readProposalExecutionReceipt });
const scope = { workspaceId: "a".repeat(32), taskId: "b".repeat(32), proposalId: "c".repeat(32) };
const receipt = { workspace_id: scope.workspaceId, task_id: scope.taskId, proposal_id: scope.proposalId,
    file_status: "replaced", application_status: "applied", code: "proposal_application_applied", cleanup_complete: true };
const Button = ({ variant, size, ...props }: Record<string, unknown>) => { void variant; void size; return createElement("button", props); };

// 转译真实组件；受控hook调度验证事件、订阅和cleanup，不冒充浏览器验收。
function harness(fetcher: typeof fetch = async () => Response.json(receipt), failStorage = false) {
    const values: unknown[] = [];
    const refs: { current: unknown }[] = [];
    const effects: (() => (() => void))[] = [];
    let stateIndex = 0, refIndex = 0, mounted = false;
    const entries = new Map<string, string>();
    const modules = compile("proposal-execution-actions.tsx", "({ ExecutionSession, ProposalExecutionActions })", {
        useState(initial: unknown) {
            const index = stateIndex++;
            if (!(index in values)) values[index] = initial;
            return [values[index], (value: unknown) => { values[index] = value; }];
        },
        useRef(initial: unknown) {
            const index = refIndex++;
            return refs[index] ?? (refs[index] = { current: initial });
        },
        useEffect(effect: () => (() => void)) { if (!mounted) effects.push(effect); },
        createProposalExecutionRequest, ProposalExecutionResultPanel: Result, Button,
        window: {
            get sessionStorage() {
                if (failStorage) throw Error("PRIVATE");
                return { getItem: (key: string) => entries.get(key) ?? null, setItem: (key: string, value: string) => entries.set(key, value) };
            },
            fetch: fetcher,
        },
    });
    function render() { stateIndex = 0; refIndex = 0; return modules.ExecutionSession(scope); }
    return {
        values, entries, modules, render,
        html: () => renderToStaticMarkup(render()),
        mount() { const cleanup = effects.at(-1)!(); mounted = true; return cleanup; },
    };
}
// React元素树定位事件目标，不复制组件中的事件逻辑。
type TestNode = {
    type: unknown;
    props: {
        children: unknown;
        disabled: boolean;
        onClick: () => void;
        onChange: (event: { target: { checked: boolean } }) => void;
    };
};
function find(root: unknown, predicate: (node: TestNode) => boolean): TestNode {
    function visit(value: unknown): TestNode | null {
        if (!value || typeof value !== "object") return null;
        const node = value as TestNode;
        if (predicate(node)) return node;
        for (const child of [node.props?.children].flat(Infinity)) {
            const found = visit(child); if (found) return found;
        }
        return null;
    }
    const found = visit(root);
    assert.ok(found, "expected rendered event target");
    return found;
}
const button = (tree: unknown, label: string) => find(tree, node => node.props?.children === label);

for (const phase of ["checking", "idle", "submitting", "uncertain", "unavailable", "receipt"]) {
    test(`${phase} renders guarded actions and accurate explanation`, () => {
        const h = harness();
        h.values.push(phase === "receipt" ? { phase, scope, receipt } : { phase, scope }, false);
        const html = h.html();
        assert.ok(html.includes('aria-label="受限样例提案应用"'));
        assert.ok(html.includes('disabled=""'));
        assert.equal(html.includes("停止等待</button>"), phase === "submitting");
        assert.equal(html.includes('type="checkbox"'), phase === "idle");
        assert.ok(!html.includes("重试</button>"));
        if (phase === "receipt") assert.ok(html.includes("本次文件替换已确认"));
        if (phase === "uncertain") assert.ok(html.includes("不能据此判断文件未修改"));
    });
}
test("initial render never reads browser storage or sends requests", () => {
    const h = harness(async () => { throw Error("must not fetch"); }, true);
    assert.ok(h.html().includes("正在检查"));
});
test("storage getter failure disables actions after mount", async () => {
    const h = harness(undefined, true); h.render(); const cleanup = h.mount();
    await Promise.resolve();
    assert.ok(h.html().includes("无法安全读取")); cleanup();
});
test("explicit checkbox and repeated click use real request guard", async () => {
    let calls = 0;
    const h = harness(async () => { calls++; return Response.json(receipt); });
    h.render(); const cleanup = h.mount(); await Promise.resolve();
    button(h.render(), "应用样例提案").props.onClick();
    assert.equal(calls, 0);
    find(h.render(), n => n.type === "input").props.onChange({ target: { checked: true } });
    const apply = button(h.render(), "应用样例提案");
    assert.equal(apply.props.disabled, false);
    apply.props.onClick(); apply.props.onClick();
    // 等待真实控制器的fetch/json两个异步阶段完成。
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls, 1);
    assert.ok(h.html().includes("已确认登记应用成功"));
    assert.equal(button(h.render(), "应用样例提案").props.disabled, true);
    cleanup();
});
for (const action of ["cancel", "unmount"]) {
    test(`${action} aborts waiting and preserves guard despite late response`, async () => {
        let resolve!: (response: Response) => void;
        let signal: AbortSignal | null | undefined;
        const h = harness(async (_url, init) => {
            signal = init?.signal;
            return new Promise<Response>(yes => { resolve = yes; });
        });
        h.render(); const cleanup = h.mount(); await Promise.resolve();
        find(h.render(), n => n.type === "input").props.onChange({ target: { checked: true } });
        button(h.render(), "应用样例提案").props.onClick();
        if (action === "cancel") button(h.render(), "停止等待").props.onClick();
        else cleanup();
        const snapshot = JSON.stringify(h.values);
        assert.equal(signal?.aborted, true);
        resolve(Response.json(receipt)); await new Promise(yes => setImmediate(yes));
        assert.equal(JSON.stringify(h.values), snapshot);
        assert.equal([...h.entries.values()][0], "uncertain");
        if (action === "cancel") { assert.ok(h.html().includes("已阻止再次提交")); cleanup(); }
    });
}
test("keyed resource boundary resets lifecycle and confirmation", () => {
    const h = harness();
    const a = h.modules.ProposalExecutionActions(scope);
    for (const field of Object.keys(scope)) {
        const b = h.modules.ProposalExecutionActions({ ...scope, [field]: "d".repeat(32) });
        assert.notEqual(a.key, b.key);
    }
});
test("cleanup before initial microtask prevents unmounted state updates", async () => {
    const h = harness(); h.render(); const cleanup = h.mount(); cleanup();
    await Promise.resolve();
    assert.deepEqual(h.values[0], { phase: "checking" });
});
test("stored unknown receipt remains unknown without an enabled action", async () => {
    const h = harness();
    h.entries.set(`proposal-execution:v1:${scope.workspaceId}:${scope.taskId}:${scope.proposalId}`,
        JSON.stringify({ ...receipt, application_status: "unknown", code: "proposal_application_registration_unconfirmed" }));
    h.render(); const cleanup = h.mount(); await Promise.resolve();
    assert.ok(h.html().includes("未确认数据库登记结果"));
    assert.equal(button(h.render(), "应用样例提案").props.disabled, true);
    cleanup();
});
