import { readProposalApplicationStatus } from "../../../src/features/workbench/proposal-application-status-data.ts";
import { readFileEditProposalDecisionReceipt } from "../../../src/features/workbench/file-edit-proposal-decision-data.ts";
import { parseFileEditProposal } from "../../../src/features/chat/file-edit-proposal-view.ts";
// 编译真实TSX组件并使用React渲染，供工具卡片兼容测试共用。
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { createElement, useEffect, useRef, useState } from "react";
import { isProposalIdentifier, readFileEditProposalDetail } from "../../../src/features/workbench/file-edit-proposal-data.ts";
import ts from "typescript";
import { commandStatusLabel, parseCommandResult } from "../../../src/features/chat/command-result-view.ts";
import { classifyDiffLine, parseFileEditPreview } from "../../../src/features/chat/file-edit-preview-view.ts";

const require = createRequire(import.meta.url);
const { renderToStaticMarkup } = require("react-dom/server");
function compileComponent(file: string, name: string, dependencies: Record<string, unknown>) {
    const source = readFileSync(new URL(`../../../src/features/chat/components/${file}`, import.meta.url), "utf8");
    const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const body = ast.statements.filter(statement => !ts.isImportDeclaration(statement))
        .map(statement => statement.getText(ast)).join("\n").replace("export default function", "function");
    const code = ts.transpileModule(body, { compilerOptions: { target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React } }).outputText;
    return new Function("React", ...Object.keys(dependencies), `${code}\nreturn ${name};`)(
        { createElement }, ...Object.values(dependencies),
    );
}
const FileEditPreviewCard = compileComponent("file-edit-preview-card.tsx", "FileEditPreviewCard", { classifyDiffLine });
const FileEditProposalCard = compileComponent("file-edit-proposal-card.tsx", "FileEditProposalCard", {});
const FileEditProposalActions = compileComponent("file-edit-proposal-actions.tsx", "FileEditProposalActions", {
    useEffect, useRef, useState, readFileEditProposalDecisionReceipt,
    Button: ({ variant, size, ...props }: Record<string, unknown>) => {
        void variant;
        void size;
        return createElement("button", props);
    },
});
const ProposalApplicationStatusPanel = compileComponent("proposal-application-status.tsx", "ProposalApplicationStatusPanel", {
    useEffect, useRef, useState, isProposalIdentifier, readProposalApplicationStatus,
    Button: ({ variant, size, ...props }: Record<string, unknown>) => {
        void variant;
        void size;
        return createElement("button", props);
    },
});
const FileEditProposalDetailPanel = compileComponent("file-edit-proposal-detail.tsx", "FileEditProposalDetailPanel", {
    useEffect, useRef, useState, isProposalIdentifier, readFileEditProposalDetail, FileEditProposalActions, ProposalApplicationStatusPanel,
    // 只替换UI按钮外观，状态与详情组件使用真实React和TSX。
    Button: ({ variant, size, ...props }: Record<string, unknown>) => {
        void variant;
        void size;
        return createElement("button", props);
    },
});
const ToolResult = compileComponent("tool-result.tsx", "ToolResult", {
    commandStatusLabel, parseCommandResult, parseFileEditPreview, FileEditPreviewCard,
    parseFileEditProposal, FileEditProposalCard, FileEditProposalDetailPanel,
});
export function renderToolResult(toolName: string, result: string, taskScope?: { workspaceId: string; taskId: string }): string {
    return renderToStaticMarkup(createElement(ToolResult, { toolName, result, taskScope }));
}

// 仅注入已加载的状态，真实TSX负责渲染；不把此测试当作浏览器交互验收。
export function renderProposalDetailSnapshot(
    detail: NonNullable<ReturnType<typeof readFileEditProposalDetail>>,
): string {
    const Panel = compileComponent("file-edit-proposal-detail.tsx", "FileEditProposalDetailPanel", {
        useEffect, useRef, isProposalIdentifier, readFileEditProposalDetail, FileEditProposalActions, ProposalApplicationStatusPanel,
        useState: () => [{ status: "ready", detail }, () => {}],
        Button: ({ variant, size, ...props }: Record<string, unknown>) => {
            void variant;
            void size;
            return createElement("button", props);
        },
    });
    return renderToStaticMarkup(createElement(Panel, {
        workspaceId: detail.workspace_id,
        taskId: detail.task_id,
        proposalId: detail.proposal_id,
    }));
}


// 受控初始状态只验证真实TSX分支；交互与持久标记由PC浏览器专项验证。
export function renderProposalActionsSnapshot(
    detail: NonNullable<ReturnType<typeof readFileEditProposalDetail>>,
    phase = "checking",
): string {
    let hook = 0;
    const Actions = compileComponent("file-edit-proposal-actions.tsx", "FileEditProposalActions", {
        useEffect, useRef, readFileEditProposalDecisionReceipt,
        useState: () => [hook++ === 0 ? phase : "", () => {}],
        Button: ({ variant, size, ...props }: Record<string, unknown>) => {
            void variant;
            void size;
            return createElement("button", props);
        },
    });
    return renderToStaticMarkup(createElement(Actions, { detail, onDecided: () => {} }));
}

// 历史事件复用真实ToolResult，校验入口分派与当前任务范围传递。
export function renderHistoricalEvent(event: unknown, taskScope: { workspaceId: string; taskId: string }): string {
    const EventContent = compileComponent("../../workbench/components/task-run-panel.tsx", "EventContent", { ToolResult });
    return renderToStaticMarkup(createElement(EventContent, { event, taskScope }));
}

export function renderApplicationSnapshot(state: unknown = { phase: "idle" }): string {
    const Panel = compileComponent("proposal-application-status.tsx", "ProposalApplicationStatusPanel", {
        useEffect, useRef, isProposalIdentifier, readProposalApplicationStatus,
        useState: () => [state, () => {}],
        Button: ({ variant, size, ...props }: Record<string, unknown>) => {
            void variant;
            void size;
            return createElement("button", props);
        },
    });
    return renderToStaticMarkup(createElement(Panel, {
        workspaceId: "a".repeat(32), taskId: "b".repeat(32), proposalId: "c".repeat(32),
    }));
}
