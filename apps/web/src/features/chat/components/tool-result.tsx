import { parseTaskSampleDiff } from "../task-sample-diff-view";
import TaskSampleDiffCard from "./task-sample-diff-card";
import { parseVerification } from "../verification-view";
import VerificationCard from "./verification-card";
import { parseGitDiff } from "../git-diff-view";
import GitDiffCard from "./git-diff-card";
import { parseFileEditProposal } from "../file-edit-proposal-view";
import FileEditProposalCard from "./file-edit-proposal-card";
import { commandStatusLabel, parseCommandResult } from "../command-result-view";
import { parseFileEditPreview } from "../file-edit-preview-view";
import FileEditPreviewCard from "./file-edit-preview-card";
import FileEditProposalDetailPanel from "./file-edit-proposal-detail";
import { parseVaultSearch } from "../vault-search-view";
import VaultSearchCard from "./vault-search-card";

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
    if (toolName === "search_vault") {
        const value = parseVaultSearch(toolName, result, taskScope);
        return value ? <VaultSearchCard value={value} /> : (
            <p className="mt-3 text-sm" role="status">Vault 检索结果无法确认，未显示笔记片段。</p>
        );
    }
    if (toolName === "read_task_sample_diff") {
        const diff = parseTaskSampleDiff(toolName, result);
        return diff ? <TaskSampleDiffCard value={diff} /> : (
            <p className="mt-3 text-sm" role="status">应用样例差异格式未识别，无法确认差异结果。</p>
        );
    }
    if (toolName === "verify_task_sample") {
        const verification = parseVerification(toolName, result);
        return verification ? <VerificationCard value={verification} /> : (
            <p className="mt-3 text-sm" role="status">验证结果格式未识别，无法确认验证结论。</p>
        );
    }
    const gitDiff = parseGitDiff(toolName, result);
    if (gitDiff !== null) return <GitDiffCard value={gitDiff} />;

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

        if (toolName === "git_sample_diff") {
            formatMessage = "Git 差异结果格式未识别，显示原始文本";
        } else if (toolName === "run_command") {
            formatMessage = "命令结果格式未识别，显示原始文本";
        } else if (toolName === "create_file_edit_proposal" || toolName === "create_file_patch_proposal") {
            formatMessage = "提案回执格式未识别，显示原始文本";
        } else if (toolName === "preview_file_edit" || toolName === "preview_file_patch") {
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
