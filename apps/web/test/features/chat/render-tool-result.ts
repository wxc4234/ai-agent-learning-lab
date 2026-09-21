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
const FileEditProposalDetailPanel = compileComponent("file-edit-proposal-detail.tsx", "FileEditProposalDetailPanel", {
    useEffect, useRef, useState, isProposalIdentifier, readFileEditProposalDetail,
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
