import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import FileEditProposalDetailPanel from "../../../src/features/chat/components/file-edit-proposal-detail";

function Fixture() {
    const [second, setSecond] = useState(location.hash === "#second");
    return <main className="mx-auto max-w-3xl space-y-4 p-6">
        <h1>提案许可管理</h1>
        <button onClick={() => setSecond(value => { location.hash = value ? "first" : "second"; return !value; })}>切换提案</button>
        <FileEditProposalDetailPanel key={String(second)} workspaceId={"a".repeat(32)} taskId={"b".repeat(32)} proposalId={(second ? "d" : "c").repeat(32)} />
    </main>;
}
createRoot(document.getElementById("root")!).render(<StrictMode><Fixture /></StrictMode>);
