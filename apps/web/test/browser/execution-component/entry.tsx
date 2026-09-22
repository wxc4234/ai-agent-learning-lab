import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import ProposalExecutionActions from "../../../src/features/chat/components/proposal-execution-actions";

function Fixture() {
    const [proposal, setProposal] = useState("c".repeat(32));
    const [visible, setVisible] = useState(true);
    useEffect(() => {
        // 验收StrictMode确实执行setup→cleanup→setup，而非仅包了一层标签。
        document.documentElement.dataset.mounts = String(Number(document.documentElement.dataset.mounts ?? 0) + 1);
    }, []);
    return (
        <main className="mx-auto max-w-3xl space-y-6 p-6">
            <h1 className="text-xl font-semibold">样例应用组件隔离验收</h1>
            <p>仅测试组件；请求由测试程序响应，不调用文件执行服务。</p>
            <nav className="flex gap-3">
                <button onClick={() => setProposal("c".repeat(32))}>样例 A</button>
                <button onClick={() => setProposal("d".repeat(32))}>样例 B</button>
                <button onClick={() => setVisible(value => !value)}>{visible ? "卸载组件" : "挂载组件"}</button>
            </nav>
            <p>当前样例：{proposal[0] === "c" ? "A" : "B"}</p>
            {visible && <ProposalExecutionActions workspaceId={"a".repeat(32)} taskId={"b".repeat(32)} proposalId={proposal} />}
        </main>
    );
}
createRoot(document.getElementById("root")!).render(<StrictMode><Fixture /></StrictMode>);
