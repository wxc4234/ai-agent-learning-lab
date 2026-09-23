"use client";

import { useState } from "react";
import TaskSampleCleanupPreflightPanel from "@/features/workbench/components/task-sample-cleanup-preflight-panel";

type Scope = {
    workspaceId: string;
    sourceTaskId: string;
    siblingTaskId: string;
};

type Target = "source" | "sibling" | "unknown";

export default function TaskFixture({ workspaceId, sourceTaskId, siblingTaskId }: Scope) {
    const [target, setTarget] = useState<Target>("source");
    const taskId = target === "source"
        ? sourceTaskId
        : target === "sibling"
            ? siblingTaskId
            : "f".repeat(32);

    return (
        <main className="mx-auto max-w-3xl space-y-5 p-6">
            <h1 className="text-xl font-semibold">清理待办只读诊断链路验收</h1>
            <nav aria-label="诊断目标" className="flex flex-wrap gap-3">
                <button type="button" onClick={() => setTarget("source")}>来源任务</button>
                <button type="button" onClick={() => setTarget("sibling")}>同项目其他任务</button>
                <button type="button" onClick={() => setTarget("unknown")}>未知任务</button>
            </nav>
            <TaskSampleCleanupPreflightPanel
                key={`${workspaceId}:${taskId}`}
                workspaceId={workspaceId}
                taskId={taskId}
            />
        </main>
    );
}
