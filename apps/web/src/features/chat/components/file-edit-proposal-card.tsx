import type { FileEditProposalView } from "../file-edit-proposal-view";

export default function FileEditProposalCard({ proposal }: { proposal: FileEditProposalView }) {
    return (
        <section aria-label="文件修改提案回执" className="mt-3 min-w-0 space-y-3 text-xs">
            <div>
                <p className="font-medium text-sm">提案已保存，待审批</p>
                <p className="mt-1 text-muted-foreground">尚未写入文件</p>
                <p className="mt-1 break-all font-mono">{proposal.relativePath}</p>
            </div>
            <dl className="space-y-2">
                <div>
                    <dt className="text-muted-foreground">提案编号</dt>
                    <dd className="break-all font-mono">{proposal.proposalId}</dd>
                </div>
                <div>
                    <dt className="text-muted-foreground">创建时间（含时区）</dt>
                    {/* 保留服务端时间及偏移，避免浏览器时区或水合差异。 */}
                    <dd className="break-all"><time dateTime={proposal.createdAt}>{proposal.createdAt}</time></dd>
                </div>
            </dl>
            <details>
                <summary className="cursor-pointer text-muted-foreground">内容摘要 SHA-256</summary>
                <dl className="mt-2 space-y-2">
                    <div><dt>原文基线</dt><dd><code className="block break-all">{proposal.baselineSha256}</code></dd></div>
                    <div><dt>提案内容</dt><dd><code className="block break-all">{proposal.proposedSha256}</code></dd></div>
                </dl>
            </details>
            <p className="text-muted-foreground">
                {proposal.diffTruncated ? "Diff 已截断，审阅内容不完整" : "保存的 Diff 未截断"}
            </p>
            <p className="text-muted-foreground">
                此处仅展示创建时的回执，不包含 Diff，也不代表当前审批状态或文件基线仍有效。
            </p>
        </section>
    );
}
