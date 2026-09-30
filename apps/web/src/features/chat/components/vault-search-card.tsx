import {
    VAULT_INCOMPLETE_LABELS,
    vaultSearchCoverage,
    type VaultSearchView,
} from "../vault-search-view";

export default function VaultSearchCard({ value }: { value: VaultSearchView }) {
    return (
        <section aria-label="Vault 笔记检索" className="mt-3 min-w-0 space-y-3 rounded-md border border-border bg-background p-3">
            <h4 className="text-sm font-medium">Vault 笔记检索</h4>
            <p className="break-words text-sm [overflow-wrap:anywhere]">关键词：<code>{value.query}</code></p>
            <p className="text-xs text-muted-foreground">
                已检索 {value.searchedFiles} 个 Markdown 文件 · 读取 {value.readBytes.toLocaleString("zh-CN")} 字节
            </p>
            <p role="status" aria-label="检索覆盖状态" className="text-sm">{vaultSearchCoverage(value)}</p>
            {value.incompleteReasons.length > 0 && (
                <ul aria-label="检索不完整原因" className="space-y-1 text-xs text-muted-foreground">
                    {value.incompleteReasons.map(reason => <li key={reason}>{VAULT_INCOMPLETE_LABELS[reason]}</li>)}
                </ul>
            )}
            {value.matches.length > 0 && (
                <ol aria-label="笔记命中列表" tabIndex={0} className="max-h-96 min-w-0 space-y-3 overflow-auto rounded-md focus-visible:outline-2 focus-visible:outline-ring">
                    {value.matches.map(match => (
                        <li key={`${match.relativePath}:${match.lineNumber}`} className="min-w-0 space-y-2 border-t border-border pt-3">
                            {/* 路径是可核对的引用文本；不生成任意 URL 或文件读取请求。 */}
                            <code className="block break-all text-sm">{match.relativePath}:{match.lineNumber}</code>
                            <p className="text-xs text-muted-foreground">命中第 {match.columnNumber} 列</p>
                            <pre aria-label={`片段 ${match.relativePath}:${match.lineNumber}`} tabIndex={0} className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded border border-border p-2 font-mono text-xs leading-5 [overflow-wrap:anywhere]">
                                {match.snippet}
                            </pre>
                            {match.snippetTruncated && <p className="text-xs text-muted-foreground">片段已裁剪，不是完整命中行。</p>}
                            <details className="text-xs">
                                <summary className="cursor-pointer text-muted-foreground">本次文件 SHA-256</summary>
                                <code className="mt-1 block break-all">{match.sha256}</code>
                            </details>
                        </li>
                    ))}
                </ol>
            )}
            <p className="text-xs text-muted-foreground">引用和摘要对应本次读取，不能证明文件之后没有变化。查看历史不会重新检索。</p>
        </section>
    );
}
