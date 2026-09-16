"use client";

import { useEffect, useState } from "react";

export default function LoadingPlaceholder({ label, compact = false }: {
    label: string;
    compact?: boolean;
}) {
    const [visible, setVisible] = useState(false);

    useEffect(() => {
        // 快速本地请求不显示瞬时加载提示；慢请求才展示安静的占位。
        const timer = window.setTimeout(() => setVisible(true), 250);
        return () => window.clearTimeout(timer);
    }, []);

    if (!visible) return null;

    return (
        <div role="status" aria-label={label} className={compact ? "space-y-3 px-2 py-3" : "space-y-5 py-2"}>
            <span className="sr-only">{label}</span>
            <div aria-hidden="true" className={`${compact ? "h-3 w-3/4" : "ml-auto h-9 w-2/5"} rounded-md bg-muted/60`} />
            <div aria-hidden="true" className={`${compact ? "h-3 w-1/2" : "h-3 w-3/4"} rounded-md bg-muted/60`} />
            {!compact && <div aria-hidden="true" className="h-3 w-1/2 rounded-md bg-muted/60" />}
        </div>
    );
}
