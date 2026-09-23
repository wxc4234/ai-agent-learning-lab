"use client";

import { useState } from "react";
import TaskSampleStatusPanel from "@/features/workbench/components/task-sample-status-panel";

type Scope = {
    workspaceId: string;
    readyTaskId: string;
    siblingTaskId: string;
    missingWorkspaceId: string;
    missingTaskId: string;
};

export default function TaskFixture({ workspaceId, readyTaskId, siblingTaskId, missingWorkspaceId, missingTaskId }: Scope) {
    const [scope, setScope] = useState({ workspaceId, taskId: readyTaskId });

    return (
        <main className="mx-auto max-w-3xl space-y-4 p-6">
            <h1 className="text-xl font-semibold">任务登记状态真实只读验收</h1>
            <nav className="flex flex-wrap gap-3" aria-label="测试任务选择">
                <button type="button" onClick={() => setScope({ workspaceId, taskId: readyTaskId })}>已登记任务</button>
                <button type="button" onClick={() => setScope({ workspaceId, taskId: siblingTaskId })}>同项目其他任务</button>
                <button type="button" onClick={() => setScope({ workspaceId: missingWorkspaceId, taskId: missingTaskId })}>未登记任务</button>
                <button type="button" onClick={() => setScope({ workspaceId, taskId: "f".repeat(32) })}>未知任务</button>
            </nav>
            <TaskSampleStatusPanel workspaceId={scope.workspaceId} taskId={scope.taskId} />
        </main>
    );
}
