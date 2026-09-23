import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import TaskSampleStatusPanel from "../../../src/features/workbench/components/task-sample-status-panel";

function Fixture() {
    const [taskId, setTaskId] = useState("b".repeat(32));
    const [visible, setVisible] = useState(true);

    return (
        <div className="grid h-dvh grid-cols-[320px_minmax(0,1fr)_400px]">
            <nav className="space-y-3 border-r border-border p-5" aria-label="测试任务选择">
                <button type="button" onClick={() => setTaskId("b".repeat(32))}>任务 A</button>
                <button type="button" onClick={() => setTaskId("c".repeat(32))}>任务 B</button>
                <button type="button" onClick={() => setTaskId("invalid")}>无效任务</button>
                <button type="button" onClick={() => setVisible(value => !value)}>
                    {visible ? "关闭详情" : "打开详情"}
                </button>
            </nav>
            <main className="p-5">受限样例登记状态组件隔离验收</main>
            <aside className="min-w-0 border-l border-border p-5" aria-label="运行详情">
                {visible && (
                    <TaskSampleStatusPanel
                        workspaceId={"a".repeat(32)}
                        taskId={taskId}
                    />
                )}
            </aside>
        </div>
    );
}

createRoot(document.getElementById("root")!).render(
    <StrictMode>
        <Fixture />
    </StrictMode>,
);
