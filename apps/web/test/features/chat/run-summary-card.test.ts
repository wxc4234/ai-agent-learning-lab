import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import { createElement, type ReactElement } from "react";
import ts from "typescript";
import { cn } from "cn";
import { initialChatState, type ChatState, type CompletedRunSummary } from "../../../src/features/chat/chat-state.ts";
import { createRunSummaryMetrics } from "../../../src/features/chat/run-summary-view.ts";

const require = createRequire(import.meta.url);
const { renderToStaticMarkup } = require("react-dom/server") as {
    renderToStaticMarkup(element: ReactElement): string;
};
const summary: CompletedRunSummary = {
    stepsTaken: 0,
    metrics: {
        model_usage: null,
        model_duration_ms: null,
        tool_duration_ms: 0,
        estimated_cost_cny: "0",
        pricing: {
            model: "deepseek-chat", tier: "peak",
            cache_hit_input_cny_per_million: "0.10",
            cache_miss_input_cny_per_million: "3.0",
            output_cny_per_million: "9.0",
        },
    },
};

function source(path: string) {
    return ts.createSourceFile(path, readFileSync(new URL(path, import.meta.url), "utf8"),
        ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
}
function compile(code: string) {
    return ts.transpileModule(code, {
        compilerOptions: { target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React },
    }).outputText;
}
// 读取生产组件本体，使用真实 React 服务端渲染；不复制卡片 JSX。
const primitiveSource = source("../../../src/components/ui/card.tsx");
const primitiveFunction = primitiveSource.statements.find(ts.isFunctionDeclaration);
assert.ok(primitiveFunction);
const SharedCard = new Function("React", "cn",
    compile(primitiveFunction.getText(primitiveSource)) + "\nreturn Card;",
)({ createElement }, cn);
const cardSource = source("../../../src/features/chat/components/run-summary-card.tsx");
const cardFunction = cardSource.statements.find(ts.isFunctionDeclaration);
assert.ok(cardFunction);
const declaration = cardFunction.getText(cardSource).replace(/^export default /, "");
const Card = new Function("React", "createRunSummaryMetrics", "Card",
    compile(declaration) + "\nreturn RunSummaryCard;",
)({ createElement }, createRunSummaryMetrics, SharedCard) as (
    props: { summary: CompletedRunSummary; status: "done" | "error" },
) => ReactElement;

const footerSource = source("../../../src/features/chat/components/run-metrics-footer.tsx");
const footerFunction = footerSource.statements.find(ts.isFunctionDeclaration);
assert.ok(footerFunction);
const Footer = new Function("React", "createRunSummaryMetrics",
    compile(footerFunction.getText(footerSource).replace(/^export default /, "")) + "\nreturn RunMetricsFooter;",
)({ createElement }, createRunSummaryMetrics);

// 执行父组件真实条件，检查新底栏保持成功/失败及过期摘要隐藏语义。
const panelSource = source("../../../src/features/chat/components/chat-panel.tsx");
let expression: ts.Expression | undefined;
function visit(node: ts.Node) {
    if (ts.isJsxExpression(node) && node.expression?.getText(panelSource).includes("<RunMetricsFooter")) {
        expression = node.expression;
    }
    ts.forEachChild(node, visit);
}
visit(panelSource);
assert.ok(expression);
const renderPanelCard = new Function("React", "RunMetricsFooter", "chatState", "restoredSummary = null",
    compile(`const result = ${expression.getText(panelSource)};`) + "\nreturn result;",
);

for (const status of ["done", "error"] as const) {
    test(`summary card displays ${status} with zero and unknown values`, () => {
        const html = renderToStaticMarkup(createElement(Card, { summary, status }));
        assert.ok(html.includes(status === "error" ? "失败运行摘要" : "运行摘要"));
        assert.equal(html.includes("本次运行未成功完成"), status === "error");
        for (const value of ["0 步", "0 ms", "¥0", "暂无数据"]) assert.ok(html.includes(value));
    });
    test(`panel passes ${status} and summary to the card`, () => {
        const element = renderPanelCard({ createElement }, Footer, {
            ...initialChatState, status, runSummary: summary,
        }) as ReactElement<{ failed: boolean; summary: CompletedRunSummary }>;
        assert.equal(element.props.failed, status === "error");
        assert.equal(element.props.summary, summary);
    });
}

for (const status of ["idle", "thinking", "streaming", "done", "aborted", "error"] as const) {
    test(`panel keeps footer without summary in ${status}`, () => {
        const state: ChatState = { ...initialChatState, status };
        const element = renderPanelCard({ createElement }, Footer, state);
        assert.equal(element.props.summary, null);
        assert.ok(renderToStaticMarkup(element).includes("总 Token"));
    });
}
for (const status of ["idle", "thinking", "streaming", "aborted"] as const) {
    test(`panel hides stale summary in ${status}`, () => {
        const element = renderPanelCard({ createElement }, Footer, {
            ...initialChatState, status, runSummary: summary,
        });
        assert.equal(element.props.summary, null);
    });
}

for (const failed of [false, true]) {
    test(`compact footer preserves known, unknown and failure values: ${failed}`, () => {
        const html = renderToStaticMarkup(createElement(Footer, { summary, failed }));
        assert.ok(html.includes('本次运行指标'));
        for (const value of ['0 步', '0 ms', '¥0', '暂无数据']) assert.ok(html.includes(value));
        assert.equal(html.includes('运行失败'), failed);
    });
}

test('idle footer restores persisted metrics, but new run does not reuse them', () => {
    const restored = { status: 'done', summary };
    const idle = renderPanelCard({ createElement }, Footer, initialChatState, restored);
    assert.equal(idle.props.summary, summary);
    const busy = renderPanelCard({ createElement }, Footer, { ...initialChatState, status: 'thinking' }, restored);
    assert.equal(busy.props.summary, null);
    assert.equal(busy.props.running, true);
});
