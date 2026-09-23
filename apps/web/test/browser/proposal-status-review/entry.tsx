import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import FileEditProposalDetailPanel from "../../../src/features/chat/components/file-edit-proposal-detail";

function Fixture() {
    return (
        <main className="mx-auto max-w-3xl space-y-4 p-6">
            <h1 className="text-xl font-semibold">提案与样例状态隔离验收</h1>
            <p>两个状态由测试夹具独立返回；不连接真实后端或文件服务。</p>
            <FileEditProposalDetailPanel
                workspaceId={"a".repeat(32)}
                taskId={"b".repeat(32)}
                proposalId={"c".repeat(32)}
            />
        </main>
    );
}

createRoot(document.getElementById("root")!).render(
    <StrictMode>
        <Fixture />
    </StrictMode>,
);
