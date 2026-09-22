import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { createElement } from "react";
import { test } from "node:test";
import ts from "typescript";
import { readProposalExecutionReceipt } from "../../../src/features/workbench/proposal-execution-data.ts";

const require = createRequire(import.meta.url);
const { renderToStaticMarkup } = require("react-dom/server");
// 与现有组件测试一致：转译真实TSX，注入真实解析器，不模拟展示分支。
const source = readFileSync(new URL("../../../src/features/chat/components/proposal-execution-result.tsx", import.meta.url), "utf8");
const ast = ts.createSourceFile("panel.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const body = ast.statements.filter((statement) => !ts.isImportDeclaration(statement))
    .map((statement) => statement.getText(ast)).join("\n")
    .replace("export default function", "function");
const code = ts.transpileModule(body, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React,
} }).outputText;
const Panel = new Function("React", "readProposalExecutionReceipt", `${code}\nreturn ProposalExecutionResultPanel;`)(
    { createElement }, readProposalExecutionReceipt,
);
const scope = { workspaceId: "a".repeat(32), taskId: "b".repeat(32), proposalId: "c".repeat(32) };
function receipt(file = "replaced", status = "applied", clean: boolean | null = true, suffix = status) {
    return { workspace_id: scope.workspaceId, task_id: scope.taskId, proposal_id: scope.proposalId,
        file_status: file, application_status: status, cleanup_complete: clean,
        code: "proposal_application_" + suffix };
}
function render(raw: unknown, current = scope): string {
    return renderToStaticMarkup(createElement(Panel, { ...current, receipt: raw }));
}
const evidence = [
    ["not_attempted", null, "not_applied", "本次调用未进入文件替换"],
    ["not_replaced", true, "not_applied", "本次调用未替换文件"],
    ["not_replaced", false, "uncertain", "本次调用未替换文件"],
    ["replaced", true, "applied", "本次文件替换已确认"],
    ["replaced", false, "uncertain", "本次文件替换已确认"],
    ["uncertain", null, "uncertain", "本次文件结果无法确认"],
    ["uncertain", true, "uncertain", "本次文件结果无法确认"],
    ["uncertain", false, "uncertain", "本次文件结果无法确认"],
] as const;
for (const [file, clean, status, label] of evidence) {
    for (const unconfirmed of [false, true]) {
        test(`${file}/${clean}/${unconfirmed ? "unknown" : status} renders separate evidence`, () => {
            const raw = receipt(file, unconfirmed ? "unknown" : status, clean,
                unconfirmed ? "registration_unconfirmed" : status);
            const html = render(raw);
            assert.ok(html.includes(label));
            assert.ok(html.includes("文件结果") && html.includes("登记确认") && html.includes("临时资源"));
            assert.ok(html.includes(clean === null ? "未取得清理结果" : clean ? "临时资源清理已确认完成" : "临时资源清理未完成"));
            if (unconfirmed) {
                assert.ok(html.includes("未确认数据库登记结果"));
                assert.ok(html.includes("登记可能尚未提交，也可能已经提交"));
                assert.ok(!html.includes("已确认登记应用成功"));
            } else if (status === "not_applied") {
                assert.ok(html.includes("本次执行机会已消耗"));
            } else if (status === "uncertain") {
                assert.ok(html.includes("继续保留资源保护"));
            } else {
                assert.ok(html.includes("已确认登记应用成功"));
            }
            assert.ok(html.includes("不保证文件后来没有被修改"));
            assert.ok(!/<button|<a\s|<form|<input/.test(html));
            assert.ok(html.includes('role="status"'));
        });
    }
}
test("claim unconfirmed does not imply no execution claim", () => {
    assert.ok(render(receipt("not_attempted", "unknown", null, "claim_unconfirmed"))
        .includes("不能据此判断是否已有执行占用"));
});
for (const raw of [null, undefined, {}, receipt("not_replaced", "applied"),
    { ...receipt(), proposal_id: "d".repeat(32) }, { ...receipt(), code: "PRIVATE-error" }]) {
    test(`invalid receipt stays unknown: ${JSON.stringify(raw)}`, () => {
        const html = render(raw);
        assert.ok(html.includes('role="alert"'));
        assert.ok(html.includes("这不表示文件未修改"));
        assert.ok(!html.includes("PRIVATE"));
        assert.ok(!html.includes("已确认登记应用成功"));
    });
}
test("scope changes cannot display old success", () => {
    const raw = receipt();
    assert.ok(render(raw).includes("已确认登记应用成功"));
    const html = render(raw, { ...scope, taskId: "d".repeat(32) });
    assert.ok(html.includes('role="alert"'));
    assert.ok(!html.includes("已确认登记应用成功"));
});
test("frozen private input remains unchanged and never triggers requests", (t) => {
    const fetch = t.mock.method(globalThis, "fetch", () => { throw new Error("no requests"); });
    const raw = Object.freeze({ ...receipt(), application_token: "PRIVATE", bound_root: "/PRIVATE", proposed_content: "<script>PRIVATE</script>" });
    const before = JSON.stringify(raw);
    const html = render(raw);
    assert.ok(!html.includes("PRIVATE") && !html.includes("<script"));
    assert.equal(JSON.stringify(raw), before);
    assert.equal(fetch.mock.callCount(), 0);
});
