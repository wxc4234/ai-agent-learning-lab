import type { GitDiffView } from "../git-diff-view";

export default function GitDiffCard({ value }: { value: GitDiffView }) {
    return (
        <section aria-label="Git 样例差异" className="mt-3 min-w-0 space-y-3 rounded-md border border-border bg-background p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <h4 className="text-sm font-medium">临时 Git 样例差异</h4>
                <span className="text-xs text-muted-foreground">{value.byteCount} 字节</span>
            </div>
            <p className="text-sm">
                {value.scope === "worktree" ? "未暂存差异 · 暂存区 → 工作区" : "已暂存差异 · HEAD → 暂存区"}
            </p>
            <p className="text-xs text-muted-foreground">仅当前任务的临时样例，不代表用户项目；不含未跟踪文件，忽略子模块。</p>
            {value.diff === "" ? (
                <p className="text-sm">所选范围没有差异，不代表整个仓库干净。</p>
            ) : (
                // Git正文只作为文本展示，不解析HTML/Markdown，也不提供应用操作。
                <pre aria-label="Git 差异正文" tabIndex={0} className="max-h-80 overflow-auto whitespace-pre rounded border border-border p-3 font-mono text-xs leading-5">
                    {value.diff}
                </pre>
            )}
            <p className="text-xs text-muted-foreground">只读采集结果，不能直接视为可应用补丁。</p>
        </section>
    );
}
