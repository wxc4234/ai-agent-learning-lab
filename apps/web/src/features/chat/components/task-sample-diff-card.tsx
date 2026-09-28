import type { TaskSampleDiffView } from "../task-sample-diff-view";

export default function TaskSampleDiffCard({ value }: { value: TaskSampleDiffView }) {
    return (
        <section aria-label="应用样例差异" className="mt-3 min-w-0 space-y-3 rounded-md border border-border bg-background p-3">
            <h4 className="text-sm font-medium">应用样例差异</h4>
            <p className="text-sm">example.txt · 固定基线 <code>old</code> 后接 LF 换行 → 本次快照</p>
            <p className="text-xs text-muted-foreground">来源：当前 Task 的受控应用样例。摘要标识本次字节，不代表验证通过、后续版本或写入授权。</p>
            <dl className="space-y-2 text-xs">
                <div><dt className="text-muted-foreground">基线 SHA-256</dt><dd className="break-all font-mono">{value.baselineSha256}</dd></div>
                <div><dt className="text-muted-foreground">快照 SHA-256</dt><dd className="break-all font-mono">{value.contentSha256}</dd></div>
            </dl>
            <p className="text-xs text-muted-foreground">差异大小：{value.byteCount} 字节</p>
            {value.diff === "" ? (
                <p className="text-sm">与固定基线没有差异，不代表项目干净或测试通过。</p>
            ) : (
                // React纯文本渲染；可用键盘滚动，不解析HTML、Markdown或补丁操作。
                <pre aria-label="应用样例差异正文" tabIndex={0} className="max-h-80 overflow-auto whitespace-pre rounded border border-border p-3 font-mono text-xs leading-5">{value.diff}</pre>
            )}
            <p className="text-xs text-muted-foreground">只展示已记录快照，查看历史不会重新执行；不能直接作为可应用补丁。</p>
        </section>
    );
}
