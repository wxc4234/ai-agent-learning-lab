"use client";

import { createContext, useContext, useState, useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useWorkbench } from "../workbench-session";
import WorkbenchIcon from "./workbench-icon";
import WorkspaceSidebar from "./workspace-sidebar";

import { Button } from "@/components/ui/button";

type WorkbenchShellProps = {
    children: ReactNode;
};

const DetailsTarget = createContext<HTMLDivElement | null>(null);

// 详情随当前 Task 生命周期更新，但三栏布局与导航不随 Task 重建。
export function WorkbenchDetails({ children }: { children: ReactNode }) {
    const target = useContext(DetailsTarget);
    return target ? createPortal(children, target) : null;
}

export default function WorkbenchShell({
    children,
}: WorkbenchShellProps) {
    const [detailsTarget, setDetailsTarget] = useState<HTMLDivElement | null>(null);
    const { selection, localMode, leftOpen, rightOpen, setLeftOpen, setRightOpen } = useWorkbench();

    const containerRef = useRef<HTMLDivElement>(null);
    const dragRef = useRef<{ x: number; width: number } | null>(null);
    const [preferredWidth, setPreferredWidth] = useState(400);
    const [containerWidth, setContainerWidth] = useState(1366);
    const [dragging, setDragging] = useState(false);
    const maximum = Math.max(320, Math.min(720, containerWidth - (leftOpen ? 320 : 0) - 400));
    const detailsWidth = Math.min(maximum, Math.max(320, preferredWidth));

    useEffect(() => {
        const container = containerRef.current;
        if (!container) return;
        const observer = new ResizeObserver(([entry]) => setContainerWidth(entry.contentRect.width));
        observer.observe(container);
        // 客户端恢复宽度，不让服务端渲染与首次 hydration 产生差异。
        const frame = requestAnimationFrame(() => {
            try {
                const saved = Number(localStorage.getItem('agent-workbench-details-width'));
                if (Number.isFinite(saved) && saved >= 320 && saved <= 720) setPreferredWidth(saved);
            } catch { /* 禁用存储时仍可正常拖动。 */ }
        });
        return () => { observer.disconnect(); cancelAnimationFrame(frame); };
    }, []);

    function resize(width: number) {
        const next = Math.round(Math.max(320, Math.min(maximum, width)));
        setPreferredWidth(next);
        try { localStorage.setItem('agent-workbench-details-width', String(next)); } catch { /* 可选偏好存储。 */ }
    }

    return (
        <DetailsTarget.Provider value={detailsTarget}>
            <div
                ref={containerRef}
                className={`grid h-dvh min-h-0 overflow-hidden bg-white text-foreground dark:bg-background ${dragging ? "cursor-col-resize select-none" : ""}`}
                style={{
                    // 三栏显式指定列位置，隐藏左栏后中栏仍留在第二列。
                    // 中栏始终保留同一个节点，只改变两侧宽度。
                    // 不通过条件切换整个布局，避免聊天组件被重新挂载。
                    gridTemplateColumns: `${
                        leftOpen ? "320px" : "0px"
                    } minmax(0, 1fr) ${
                        rightOpen ? `${detailsWidth}px` : "0px"
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
                    className={`relative col-start-3 row-start-1 ${rightOpen ? "flex" : "hidden"} min-h-0 min-w-0 flex-col border-l border-border/60 bg-muted/10`}
                >
                    <div
                        role="separator"
                        aria-label="调整运行详情宽度"
                        aria-orientation="vertical"
                        aria-controls="workbench-details"
                        aria-valuemin={320}
                        aria-valuemax={maximum}
                        aria-valuenow={detailsWidth}
                        aria-valuetext={`${detailsWidth} 像素`}
                        tabIndex={0}
                        title="拖动调整宽度；方向键微调，双击重置"
                        className="absolute -left-1 top-0 z-20 h-full w-2 touch-none cursor-col-resize hover:bg-primary/20 focus-visible:bg-primary/20 focus-visible:outline-none"
                        onPointerDown={event => {
                            if (event.button !== 0) return;
                            event.preventDefault();
                            event.currentTarget.focus();
                            event.currentTarget.setPointerCapture(event.pointerId);
                            dragRef.current = { x: event.clientX, width: detailsWidth };
                            setDragging(true);
                        }}
                        onPointerMove={event => {
                            if (dragRef.current) resize(dragRef.current.width + dragRef.current.x - event.clientX);
                        }}
                        onPointerUp={event => {
                            dragRef.current = null;
                            setDragging(false);
                            if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
                        }}
                        onLostPointerCapture={() => { dragRef.current = null; setDragging(false); }}
                        onPointerCancel={() => { dragRef.current = null; setDragging(false); }}
                        onDoubleClick={() => resize(400)}
                        onKeyDown={event => {
                            const step = event.shiftKey ? 48 : 16;
                            const next = event.key === 'ArrowLeft' ? detailsWidth + step
                                : event.key === 'ArrowRight' ? detailsWidth - step
                                : event.key === 'Home' ? 320 : event.key === 'End' ? maximum : null;
                            if (next !== null) { event.preventDefault(); resize(next); }
                        }}
                    />
                    <h2 className="flex h-14 shrink-0 items-center border-b border-border/50 px-5 text-base font-medium">
                        运行详情
                    </h2>
                    {/* 滚动内容与拖动边界分开，改变宽度不重建当前聊天或详情。 */}
                    <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-5">
                        <div ref={setDetailsTarget} />
                    </div>
                </aside>
            </div>
        </DetailsTarget.Provider>
    );
}
