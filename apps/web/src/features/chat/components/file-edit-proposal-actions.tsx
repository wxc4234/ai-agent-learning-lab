"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import type {
    FileEditProposalDetail,
} from "../../workbench/file-edit-proposal-data";
import {
    readFileEditProposalDecisionReceipt,
    type FileEditProposalDecision,
} from "../../workbench/file-edit-proposal-decision-data";

type Phase =
    | "checking"
    | "idle"
    | "submitting"
    | "uncertain"
    | "approved"
    | "rejected"
    | "unavailable";

const UNCERTAIN =
    "审批结果未确认，请重新读取详情核对；暂不能再次提交。";

// 只把明确表示本次请求未执行的错误作为可恢复失败。
// 状态冲突另行处理，不能继续使用旧pending详情提交。
const SAFE_FAILURES: Record<string, string> = {
    "400:invalid_workspace_request": "审批请求无法解析，请重新读取详情后操作。",
    "401:invalid_login_session": "身份无效，请检查本地运行配置。",
    "403:local_mode_required": "审批功能仅支持本地模式。",
    "403:local_access_rejected": "本地服务拒绝访问，请检查运行配置。",
    "403:workspace_origin_rejected": "请求来源不被允许。",
    "404:workspace_not_accessible": "提案不存在或不可访问。",
    "409:proposal_binding_changed":
        "目录绑定已变化，不能批准；可以拒绝此提案。",
    "409:proposal_diff_incomplete":
        "Diff已截断，不能批准；可以拒绝此提案。",
    "415:unsupported_workspace_content_type": "审批请求类型不符合要求。",
    "422:invalid_proposal_decision_input": "审批请求参数不符合要求。",
    "422:proposal_decision_invalid": "审批决定不符合要求。",
    "499:proposal_request_cancelled": "本次请求尚未转发审批。",
};

function readGuard(key: string): Phase {
    try {
        const value = window.sessionStorage.getItem(key);

        if (value === null) {
            return "idle";
        }
        if (value === "approved" || value === "rejected") {
            return value;
        }

        // 未知存储值同样保守阻止提交，不能当成没有历史请求。
        return "uncertain";
    } catch {
        // 无法保存防重复标记时，不允许发起写请求。
        return "unavailable";
    }
}

function responseCode(raw: unknown): string | null {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
        return null;
    }

    const record = raw as Record<string, unknown>;
    return typeof record.code === "string" ? record.code : null;
}

export default function FileEditProposalActions({
    detail,
    onDecided,
}: {
    detail: FileEditProposalDetail;
    onDecided: (decision: FileEditProposalDecision) => void;
}) {
    const [phase, setPhase] = useState<Phase>("checking");
    const [message, setMessage] = useState("");

    // React状态更新不是同步锁；ref用于阻止同一实例中的连续点击。
    const busyRef = useRef(false);
    const aliveRef = useRef(false);
    const controllerRef = useRef<AbortController | null>(null);

    const key =
        "file-edit-proposal-decision:v1:"
        + `${detail.workspace_id}:${detail.task_id}:${detail.proposal_id}`;

    useEffect(() => {
        let active = true;
        aliveRef.current = true;

        // 服务端与客户端首次都显示checking，避免读取浏览器存储造成水合差异。
        queueMicrotask(() => {
            if (active) {
                setPhase(readGuard(key));
            }
        });

        return () => {
            active = false;
            aliveRef.current = false;
            controllerRef.current?.abort();
            controllerRef.current = null;

            // 卸载取消只停止等待，不清除持久标记，也不宣称事务已回滚。
        };
    }, [key]);

    const terminal =
        detail.status !== "pending"
            ? detail.status
            : phase === "approved" || phase === "rejected"
                ? phase
                : null;

    const canSubmit = (
        detail.status === "pending"
        && terminal === null
        && phase === "idle"
    );

    async function submit(decision: FileEditProposalDecision) {
        if (
            !canSubmit
            || busyRef.current
            || (decision === "approved" && detail.diff_truncated)
        ) {
            return;
        }

        // 同一标签页的其他实例可能已经提交，点击时必须再次同步检查。
        const existing = readGuard(key);
        if (existing !== "idle") {
            setPhase(existing);
            return;
        }

        try {
            // 必须先保存标记再发请求。存储失败时不能继续提交。
            window.sessionStorage.setItem(key, "uncertain");
        } catch {
            setPhase("unavailable");
            return;
        }

        busyRef.current = true;
        setPhase("submitting");
        setMessage("");

        const controller = new AbortController();
        controllerRef.current = controller;

        // 浏览器等待预算略大于BFF的20秒上游预算。
        const signal = AbortSignal.any([
            controller.signal,
            AbortSignal.timeout(25_000),
        ]);

        try {
            const response = await fetch(
                `/api/workspaces/${detail.workspace_id}`
                    + `/tasks/${detail.task_id}`
                    + `/file-edit-proposals/${detail.proposal_id}/decision`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ decision }),
                    signal,
                    cache: "no-store",
                    redirect: "error",
                },
            );

            signal.throwIfAborted();

            const raw: unknown = await response.json();

            signal.throwIfAborted();

            if (!aliveRef.current) {
                return;
            }

            if (response.status === 200) {
                const receipt = readFileEditProposalDecisionReceipt(
                    raw,
                    detail.workspace_id,
                    detail.task_id,
                    detail.proposal_id,
                    decision,
                );

                if (receipt === null) {
                    setPhase("uncertain");
                    return;
                }

                try {
                    // 保留已确认终态，防止其他组件的旧pending快照再次启用操作。
                    window.sessionStorage.setItem(key, receipt.status);
                } catch {
                    // 写终态失败时，提交前的未确认标记仍应保留。
                    // 本次已核对成功回执，可以更新当前页面的真实状态。
                }

                setPhase(receipt.status);
                onDecided(receipt.status);
                return;
            }

            const code = responseCode(raw);

            if (
                response.status === 409
                && code === "proposal_state_conflict"
            ) {
                setMessage("提案状态已变化，请重新读取详情核对。");
                setPhase("uncertain");
                return;
            }

            const errorKey = `${response.status}:${code}`;
            if (code !== null && Object.hasOwn(SAFE_FAILURES, errorKey)) {
                try {
                    // 只有明确的未执行结果才能解除本次提交标记。
                    window.sessionStorage.removeItem(key);
                } catch {
                    setPhase("unavailable");
                    return;
                }

                setMessage(SAFE_FAILURES[errorKey]);
                setPhase("idle");
                return;
            }

            // 未知状态、未知错误码和结果未确认都保持阻止重提。
            setPhase("uncertain");
        } catch {
            if (aliveRef.current) {
                setPhase("uncertain");
            }
        } finally {
            busyRef.current = false;

            if (controllerRef.current === controller) {
                controllerRef.current = null;
            }
        }
    }

    if (terminal !== null) {
        return (
            <p role="status" className="text-xs font-medium">
                {terminal === "approved"
                    ? "已确认批准此提案，尚未应用到文件。"
                    : "已确认拒绝此提案。"}
            </p>
        );
    }

    return (
        <section
            aria-label="提案审批"
            className="space-y-2 border-t border-border pt-3"
        >
            <p className="text-xs text-muted-foreground">
                请审阅上方保存的 Diff。批准只记录决定，不会写入文件。
            </p>

            <div className="flex flex-wrap gap-2">
                <Button
                    type="button"
                    size="sm"
                    disabled={!canSubmit || detail.diff_truncated}
                    onClick={() => void submit("approved")}
                >
                    批准提案
                </Button>
                <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={!canSubmit}
                    onClick={() => void submit("rejected")}
                >
                    拒绝提案
                </Button>
            </div>

            {detail.diff_truncated && (
                <p className="text-xs text-muted-foreground">
                    Diff 已截断，不能直接批准；仍可拒绝此提案。
                </p>
            )}

            {phase === "checking" && (
                <p role="status" className="text-xs text-muted-foreground">
                    正在检查本标签页的提交记录…
                </p>
            )}

            {phase === "submitting" && (
                <p role="status" className="text-xs text-muted-foreground">
                    正在提交决定，请勿重复操作。
                    收起详情或切换任务不会撤销审批。
                </p>
            )}

            {phase === "uncertain" && (
                <p role="alert" className="text-xs text-destructive">
                    {message || UNCERTAIN}
                    {" "}即使重新查询仍为待审批，也不能证明旧请求已停止。
                </p>
            )}

            {phase === "unavailable" && (
                <p role="alert" className="text-xs text-destructive">
                    无法访问本标签页的提交记录，暂不能审批。
                    可以继续读取详情核对状态。
                </p>
            )}

            {phase === "idle" && message && (
                <p role="alert" className="text-xs text-destructive">
                    {message}
                </p>
            )}
        </section>
    );
}
