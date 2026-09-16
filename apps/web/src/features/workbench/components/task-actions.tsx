"use client";

import { useRef, useState } from "react";
import { AlertDialog } from "radix-ui";
import { Button } from "@/components/ui/button";
import type { TaskItem } from "../task-data";
import type { WorkspaceListItem } from "@/features/workspaces/workspace-list";
import { useWorkbench } from "../workbench-session";

function TrashIcon() {
    return (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="size-[18px] shrink-0" aria-hidden="true">
            <path d="M3 6h18M9 6V4h6v2M5 6l1 14h12l1-14M10 10v6M14 10v6" />
        </svg>
    );
}

export default function TaskActions({ workspace, task }: {
    workspace: WorkspaceListItem;
    task: TaskItem;
}) {
    const { busy, deletion, deleteTask } = useWorkbench();
    const [confirmOpen, setConfirmOpen] = useState(false);
    const triggerRef = useRef<HTMLButtonElement>(null);
    const blocked = busy || deletion?.phase === "pending" ||
        deletion?.phase === "checking" || deletion?.phase === "uncertain";

    return (
        <AlertDialog.Root open={confirmOpen} onOpenChange={setConfirmOpen}>
            <AlertDialog.Trigger asChild>
                <Button
                    ref={triggerRef}
                    variant="ghost"
                    size="icon-sm"
                    disabled={blocked}
                    aria-label={`删除任务：${task.title}`}
                    title="删除空任务"
                    className="mr-1 size-7 shrink-0 text-muted-foreground opacity-0 group-hover/task:opacity-100 group-focus-within/task:opacity-100 hover:bg-black/5 hover:text-destructive dark:hover:bg-muted disabled:opacity-0 group-hover/task:disabled:opacity-40 group-focus-within/task:disabled:opacity-40 [@media(hover:none)]:opacity-100"
                >
                    <TrashIcon />
                </Button>
            </AlertDialog.Trigger>
            <AlertDialog.Portal>
                <AlertDialog.Overlay className="fixed inset-0 z-50 bg-black/25 backdrop-blur-[2px]" />
                <AlertDialog.Content
                    className="fixed left-1/2 top-1/2 z-50 w-[min(440px,calc(100vw-48px))] -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-border bg-background p-6 shadow-xl outline-none"
                    onCloseAutoFocus={event => {
                        event.preventDefault();
                        triggerRef.current?.focus();
                    }}
                >
                    <AlertDialog.Title className="text-xl font-semibold tracking-tight">
                        删除这个任务？
                    </AlertDialog.Title>
                    <AlertDialog.Description className="mt-3 text-[18px] leading-relaxed text-muted-foreground">
                        <span className="mb-2 block break-words font-medium text-foreground">{task.title}</span>
                        仅支持删除没有消息和运行记录的空任务。删除后无法恢复。
                    </AlertDialog.Description>
                    <div className="mt-6 flex justify-end gap-2">
                        <AlertDialog.Cancel asChild>
                            <Button variant="outline" className="text-[18px]">取消</Button>
                        </AlertDialog.Cancel>
                        <AlertDialog.Action asChild>
                            <Button
                                variant="destructive"
                                className="text-[18px]"
                                disabled={blocked}
                                onClick={() => { void deleteTask(workspace, task); }}
                            >
                                删除任务
                            </Button>
                        </AlertDialog.Action>
                    </div>
                </AlertDialog.Content>
            </AlertDialog.Portal>
        </AlertDialog.Root>
    );
}
