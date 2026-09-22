import {
    readProposalExecutionReceipt,
    type ProposalExecutionReceipt,
} from "../../workbench/proposal-execution-data";

type Props = {
    workspaceId: string;
    taskId: string;
    proposalId: string;
    receipt: unknown;
};

const FILE_LABELS: Record<ProposalExecutionReceipt["file_status"], string> = {
    not_attempted: "本次调用未进入文件替换",
    not_replaced: "本次调用未替换文件",
    replaced: "本次文件替换已确认",
    uncertain: "本次文件结果无法确认",
};
const APPLICATION_LABELS: Record<ProposalExecutionReceipt["application_status"], string> = {
    applied: "已确认登记应用成功",
    not_applied: "已确认登记本次未应用",
    uncertain: "已确认登记结果或清理不确定",
    unknown: "未确认数据库登记结果",
};

export default function ProposalExecutionResultPanel({
    workspaceId, taskId, proposalId, receipt,
}: Props) {
    // 在展示边界重新核对资源和协议；不依赖调用方的类型断言。
    const result = readProposalExecutionReceipt(receipt, workspaceId, taskId, proposalId);
    if (!result) {
        return (
            <section aria-label="提案执行回执" className="min-w-0 space-y-2 border-t border-border pt-3 text-sm">
                <p className="font-medium">本次执行回执</p>
                <p role="alert" className="text-destructive">执行回执无法识别或与当前提案不匹配。</p>
                <p className="text-muted-foreground">这不表示文件未修改，不能据此再次执行。</p>
            </section>
        );
    }

    const cleanupLabel = result.cleanup_complete === null
        ? "未取得清理结果"
        : result.cleanup_complete ? "临时资源清理已确认完成" : "临时资源清理未完成";
    // 未确认领取与未确认登记具有不同边界，不统一写成“执行失败”。
    const explanation = result.code === "proposal_application_claim_unconfirmed"
        ? "领取结果未确认；本次调用没有开始替换，但不能据此判断是否已有执行占用。"
        : result.code === "proposal_application_registration_unconfirmed"
            ? "文件结果与数据库登记确认是两件事；登记可能尚未提交，也可能已经提交。"
            : result.application_status === "not_applied"
                ? "本次执行机会已消耗；未应用不代表可以重新执行同一提案。"
                : result.application_status === "uncertain"
                    ? "结果或清理存在不确定性，继续保留资源保护，不自动重试或释放占用。"
                    : "文件替换和成功登记均已确认。";

    return (
        <section aria-label="提案执行回执" className="min-w-0 space-y-2 border-t border-border pt-3 text-sm">
            <p className="font-medium">本次执行回执</p>
            <div role="status" className="space-y-2">
                <dl className="space-y-2 break-words">
                    <div><dt className="text-muted-foreground">文件结果</dt><dd>{FILE_LABELS[result.file_status]}</dd></div>
                    <div><dt className="text-muted-foreground">登记确认</dt><dd>{APPLICATION_LABELS[result.application_status]}</dd></div>
                    <div><dt className="text-muted-foreground">临时资源</dt><dd>{cleanupLabel}</dd></div>
                </dl>
                <p>{explanation}</p>
            </div>
            <p className="text-muted-foreground">这是本次调用的回执，不保证文件后来没有被修改；此处不执行修改或自动重试。</p>
        </section>
    );
}
