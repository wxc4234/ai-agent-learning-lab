"use client";

import { type ReactNode } from "react";
import { useWorkbench } from "../workbench-session";
import WorkbenchIcon from "./workbench-icon";
import WorkspaceSidebar from "./workspace-sidebar";

import { Button } from "@/components/ui/button";

type WorkbenchShellProps = {
    children: ReactNode;
    details: ReactNode;
};

export default function WorkbenchShell({
    children,
    details,
}: WorkbenchShellProps) {
    const { selection, localMode, leftOpen, rightOpen, setLeftOpen, setRightOpen } = useWorkbench();

    return (
        <div
            className="grid h-dvh min-h-0 overflow-hidden bg-white text-foreground dark:bg-background"
            style={{
                // 三栏显式指定列位置，隐藏左栏后中栏仍留在第二列。
                // 中栏始终保留同一个节点，只改变两侧宽度。
                // 不通过条件切换整个布局，避免聊天组件被重新挂载。
                gridTemplateColumns: `${
                    leftOpen ? "320px" : "0px"
                } minmax(0, 1fr) ${
                    rightOpen ? "340px" : "0px"
                }`,
            }}
        >
            <aside
                id="workbench-navigation"
                aria-label="工作台导航"
                hidden={!leftOpen}
                className="col-start-1 row-start-1 min-h-0 overflow-y-auto border-r border-black/5 bg-[#f0f2eb] dark:border-border dark:bg-card"
            >
                <div className="flex min-h-full flex-col px-3 py-3">
                    <p className="px-2 py-3 text-base font-semibold">
                        Agent 工作台
                    </p>

                    {/* 项目数据由侧栏独立管理，不改变中间聊天组件的挂载位置。 */}
                    {localMode && <WorkspaceSidebar />}

                    <p className="mt-auto px-2 pt-8 text-xs text-muted-foreground/70">
                        AI Agent Learning Lab
                    </p>
                </div>
            </aside>

            <section className="col-start-2 row-start-1 flex min-h-0 min-w-0 flex-col">
                <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-border/50 px-4">
                    <div className="flex min-w-0 items-center gap-3">
                        <Button
                            type="button"
                            variant="ghost"
                            size="icon-sm"
                            title={leftOpen ? "收起导航" : "展开导航"}
                            aria-label={leftOpen ? "收起导航" : "展开导航"}
                            aria-controls="workbench-navigation"
                            aria-expanded={leftOpen}
                            onClick={() => setLeftOpen(!leftOpen)}
                        >
                            <WorkbenchIcon name="left" />
                        </Button>

                        <h1 className="truncate text-sm font-medium">
                            {selection?.task?.title ?? "新对话"}
                        </h1>
                    </div>

                    <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        title={rightOpen ? "收起详情" : "展开详情"}
                        aria-label={rightOpen ? "收起详情" : "展开详情"}
                        aria-controls="workbench-details"
                        aria-expanded={rightOpen}
                        onClick={() => setRightOpen(!rightOpen)}
                    >
                        <WorkbenchIcon name="right" />
                    </Button>
                </header>

                {/* main 只占剩余高度，回复滚动区与底部输入由聊天组件排列。 */}
                <main className="flex min-h-0 min-w-0 flex-1 flex-col">
                    {children}
                </main>
            </section>

            <aside
                id="workbench-details"
                aria-label="运行详情"
                hidden={!rightOpen}
                className="col-start-3 row-start-1 min-h-0 overflow-y-auto border-l border-border/50 p-4"
            >
                <h2 className="mb-4 text-xs font-medium text-muted-foreground">
                    运行详情
                </h2>

                {/* 隐藏侧栏时保留其节点，恢复后仍展示当前运行数据。 */}
                {details}
            </aside>
        </div>
    );
}
