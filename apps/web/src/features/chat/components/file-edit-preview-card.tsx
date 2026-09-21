import {
    classifyDiffLine,
    type DiffLineKind,
    type FileEditPreviewView,
} from "../file-edit-preview-view";

const lineStyles: Record<DiffLineKind, string> = {
    metadata: "text-muted-foreground",
    added: "bg-emerald-500/10 text-emerald-800 dark:text-emerald-300",
    removed: "bg-red-500/10 text-red-800 dark:text-red-300",
    context: "text-foreground",
};

export default function FileEditPreviewCard({
    preview,
}: {
    preview: FileEditPreviewView;
}) {
    // 不解析每行JSON转义，保留后端用于审阅的换行表示。
    const lines = preview.diff.split("\n");

    return (
        <section
            aria-label="文件修改预览"
            className="mt-3 min-w-0 space-y-3"
        >
            <div>
                <p className="font-medium">仅预览，未写入</p>
                <p className="mt-1 break-all font-mono text-xs">
                    {preview.relativePath}
                </p>
            </div>

            <dl className="grid grid-cols-2 gap-2 text-xs">
                <div>
                    <dt className="text-muted-foreground">修改前</dt>
                    <dd>{preview.beforeByteCount} 字节</dd>
                </div>
                <div>
                    <dt className="text-muted-foreground">预览修改后</dt>
                    <dd>{preview.afterByteCount} 字节</dd>
                </div>
            </dl>

            <details className="text-xs">
                <summary className="cursor-pointer text-muted-foreground">
                    原文基线 SHA-256
                </summary>
                <code className="mt-2 block break-all">
                    {preview.baselineSha256}
                </code>
                <p className="mt-1 text-muted-foreground">
                    对应生成预览时读取的内容，不代表批准或写入。
                </p>
            </details>

            <p className="text-xs text-muted-foreground">
                {preview.diffTruncated
                    ? "Diff 已截断，审阅内容不完整"
                    : "Diff 未截断"}
            </p>

            <div className="min-w-0">
                <p className="text-xs font-medium">修改 Diff</p>
                <p className="mt-1 text-xs text-muted-foreground">
                    行内使用 JSON 转义展示换行差异，仅供审阅，
                    不能直接用于 git apply。
                </p>

                {/* React按文本转义内容，不能把文件内容当HTML或Markdown执行。 */}
                <pre
                    aria-label="修改 Diff"
                    tabIndex={0}
                    className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-md border border-border bg-background p-2 font-mono text-xs"
                >
                    {lines.map((line, index) => (
                        <span
                            key={index}
                            className={lineStyles[classifyDiffLine(line)]}
                        >
                            {line}
                            {index < lines.length - 1 ? "\n" : ""}
                        </span>
                    ))}
                </pre>
            </div>
        </section>
    );
}
