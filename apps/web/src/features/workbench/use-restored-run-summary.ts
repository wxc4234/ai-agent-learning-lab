'use client';

import { useEffect, useState } from 'react';
import { loadLatestRunSummary, type MetricsTask } from './latest-run-summary';
import type { HistoricalRunSummary } from './historical-run-summary';

export function useRestoredRunSummary(task: MetricsTask | null, enabled: boolean) {
    const [result, setResult] = useState<{ task: MetricsTask; summary: HistoricalRunSummary | null; failed: boolean } | null>(null);
    const [retry, setRetry] = useState(0);
    useEffect(() => {
        if (!task || !enabled) return;
        const controller = new AbortController();
        const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(25000)]);
        let active = true;
        void loadLatestRunSummary(task, signal).then(summary => {
            if (active) setResult({ task, summary, failed: false });
        }).catch(() => {
            if (active) setResult({ task, summary: null, failed: true });
        });
        // 切会话和开始新运行后取消恢复，迟到结果不能覆盖本轮统计。
        return () => { active = false; controller.abort(); };
    }, [task, enabled, retry]);
    const current = enabled && result?.task === task ? result : null;
    return { summary: current?.summary ?? null, failed: current?.failed ?? false,
        retry: () => setRetry(value => value + 1) };
}
