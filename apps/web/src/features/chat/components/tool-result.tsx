import { parseFileEditProposal } from "../file-edit-proposal-view";
import FileEditProposalCard from "./file-edit-proposal-card";
import { commandStatusLabel, parseCommandResult } from "../command-result-view";
import { parseFileEditPreview } from "../file-edit-preview-view";
import FileEditPreviewCard from "./file-edit-preview-card";
import FileEditProposalDetailPanel from "./file-edit-proposal-detail";

export default function ToolResult({
    toolName,
    result,
    taskScope,
}: {
    toolName: string;
    result: string;
    taskScope?: {
        workspaceId: string;
        taskId: string;
    };
}) {
    const proposal = parseFileEditProposal(toolName, result);

    if (proposal !== null) {
        return (
            <div className="min-w-0">
                <FileEditProposalCard proposal={proposal} />

                {taskScope && (
                    <FileEditProposalDetailPanel
                        key={
                            `${taskScope.workspaceId}:`
                            + `${taskScope.taskId}:`
                            + proposal.proposalId
                        }
                        workspaceId={taskScope.workspaceId}
                        taskId={taskScope.taskId}
                        proposalId={proposal.proposalId}
                    />
                )}
            </div>
        );
    }

    const preview = parseFileEditPreview(toolName, result);

    if (preview !== null) {
        return <FileEditPreviewCard preview={preview} />;
    }

    const command = parseCommandResult(toolName, result);
    if (!command) {
        let formatMessage: string | null = null;

        if (toolName === "run_command") {
            formatMessage = "命令结果格式未识别，显示原始文本";
        } else if (toolName === "create_file_edit_proposal") {
            formatMessage = "提案回执格式未识别，显示原始文本";
        } else if (toolName === "preview_file_edit") {
            formatMessage = "预览结果格式未识别，显示原始文本";
        }

        return (
            <div className="mt-3 min-w-0">
                {formatMessage !== null && (
                    <p className="text-xs text-muted-foreground">
                        {formatMessage}
                    </p>
                )}
                <pre
                    aria-label="工具结果"
                    tabIndex={0}
                    className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all text-sm"
                >
                    {result}
                </pre>
            </div>
        );
    }
    const fact = (value: boolean | null) => value === null ? "未知" : value ? "是" : "否";
    return (
        <section aria-label="命令结果" className="mt-3 min-w-0 space-y-3">
            <p className="font-medium">{commandStatusLabel(command)}</p>
            <dl className="grid grid-cols-2 gap-2 text-xs">
                <div><dt className="text-muted-foreground">退出码</dt><dd>{command.exit_code ?? "未取得"}</dd></div>
                <div><dt className="text-muted-foreground">命令耗时</dt><dd>{command.duration_ms} ms</dd></div>
                <div><dt className="text-muted-foreground">OOM 终止</dt><dd>{fact(command.oom_killed)}</dd></div>
                <div><dt className="text-muted-foreground">Docker 服务错误</dt><dd>{fact(command.daemon_error)}</dd></div>
            </dl>
            {(["stdout", "stderr"] as const).map((channel) => (
                <div key={channel} className="min-w-0">
                    <p className="text-xs font-medium">{channel}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                        {command[`${channel}_truncated`] ? "输出已截断，仅显示已捕获部分" : "输出未截断"}
                    </p>
                    {/* 输出只作为文本渲染；每路独立限高，允许键盘聚焦后滚动。 */}
                    <pre aria-label={channel} tabIndex={0} className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-md border border-border bg-background p-2 text-xs">
                        {command[channel] || "（无输出）"}
                    </pre>
                </div>
            ))}
        </section>
    );
}
