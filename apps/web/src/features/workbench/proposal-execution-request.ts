import { isProposalIdentifier } from "./file-edit-proposal-data.ts";
import {
    readProposalExecutionReceipt,
    type ProposalExecutionReceipt,
} from "./proposal-execution-data.ts";

export type ProposalExecutionScope = Readonly<{
    workspaceId: string;
    taskId: string;
    proposalId: string;
}>;

type Outcome =
    | { phase: "idle" }
    | { phase: "submitting" }
    | { phase: "receipt"; receipt: Readonly<ProposalExecutionReceipt> }
    | { phase: "uncertain" }
    | { phase: "unavailable" };

export type ProposalExecutionRequestState =
    | Readonly<{ phase: "empty"; scope: null }>
    | Readonly<Outcome & { scope: ProposalExecutionScope }>;

type Dependencies = {
    // 页面装配时传入sessionStorage；禁止以临时内存存储伪装刷新保护。
    storage: Pick<Storage, "getItem" | "setItem">;
    fetch: typeof globalThis.fetch;
    timeout?: (milliseconds: number) => AbortSignal;
};

function key(scope: ProposalExecutionScope): string {
    return "proposal-execution:v1:"
        + `${scope.workspaceId}:${scope.taskId}:${scope.proposalId}`;
}

/** 本标签页请求管理；不是后端授权、跨标签页锁或事务回滚机制。 */
export function createProposalExecutionRequest(dependencies: Dependencies) {
    let state: ProposalExecutionRequestState = Object.freeze({ phase: "empty", scope: null });
    let controller: AbortController | null = null;
    let generation = 0;
    let disposed = false;
    const listeners = new Set<() => void>();

    function publish(next: ProposalExecutionRequestState) {
        state = Object.freeze(next);
        // 订阅方异常不能改变请求结果或绕过已保存的提交标记。
        for (const listener of listeners) {
            try { listener(); } catch { /* 保留请求状态。 */ }
        }
    }

    function stored(scope: ProposalExecutionScope): Outcome {
        try {
            const value = dependencies.storage.getItem(key(scope));
            if (value === null) return { phase: "idle" };
            if (value === "uncertain") return { phase: "uncertain" };
            const receipt = readProposalExecutionReceipt(
                JSON.parse(value), scope.workspaceId, scope.taskId, scope.proposalId,
            );
            return receipt === null
                ? { phase: "uncertain" }
                : { phase: "receipt", receipt: Object.freeze(receipt) };
        } catch {
            // 存储不可用或损坏时禁止提交，不把它解释成没有历史请求。
            return { phase: "unavailable" };
        }
    }

    function select(scope: ProposalExecutionScope | null) {
        if (disposed) return;
        if (scope && ![
            scope.workspaceId, scope.taskId, scope.proposalId,
        ].every(isProposalIdentifier)) {
            throw new Error("Invalid proposal execution scope");
        }
        if (scope && state.scope && key(scope) === key(state.scope)) return;
        generation += 1;
        controller?.abort();
        controller = null;
        if (scope === null) {
            publish({ phase: "empty", scope: null });
            return;
        }
        // 复制标识，调用方之后修改原对象不会改变正在执行的范围。
        const snapshot = Object.freeze({
            workspaceId: scope.workspaceId,
            taskId: scope.taskId,
            proposalId: scope.proposalId,
        });
        publish({ ...stored(snapshot), scope: snapshot });
    }

    async function submit(): Promise<void> {
        if (disposed || state.phase !== "idle") return;
        const scope = state.scope;
        // 点击时重新检查共享标记，防止同标签页另一个实例先行提交。
        const prior = stored(scope);
        if (prior.phase !== "idle") {
            publish({ ...prior, scope });
            return;
        }
        try {
            // 必须先持久标记后发请求。任何后续失败都不自动清除此标记。
            dependencies.storage.setItem(key(scope), "uncertain");
        } catch {
            publish({ phase: "unavailable", scope });
            return;
        }
        const current = ++generation;
        const active = new AbortController();
        controller = active;
        publish({ phase: "submitting", scope });
        const isCurrent = () => !disposed && generation === current;
        try {
            const signal = AbortSignal.any([
                active.signal,
                (dependencies.timeout ?? AbortSignal.timeout)(25_000),
            ]);
            signal.throwIfAborted();
            const response = await dependencies.fetch(
                `/api/workspaces/${scope.workspaceId}/tasks/${scope.taskId}`
                    + `/file-edit-proposals/${scope.proposalId}/apply`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ action: "apply" }),
                    signal,
                    cache: "no-store",
                    redirect: "error",
                },
            );
            signal.throwIfAborted();
            if (response.status !== 200) {
                await response.body?.cancel();
                // 即使明确拒绝，本课也不自动解锁或推断此前请求未写入。
                if (isCurrent()) publish({ phase: "uncertain", scope });
                return;
            }
            const raw: unknown = await response.json();
            signal.throwIfAborted();
            if (!isCurrent()) return;
            const receipt = readProposalExecutionReceipt(
                raw, scope.workspaceId, scope.taskId, scope.proposalId,
            );
            if (receipt === null) {
                publish({ phase: "uncertain", scope });
                return;
            }
            try {
                // 保存公开回执，刷新后仍可保留unknown及文件结果证据。
                dependencies.storage.setItem(key(scope), JSON.stringify(receipt));
            } catch {
                // 写回执失败不否定已核对的回执；提交前标记继续防重复。
            }
            publish({ phase: "receipt", scope, receipt: Object.freeze(receipt) });
        } catch {
            if (isCurrent()) publish({ phase: "uncertain", scope });
        } finally {
            if (controller === active) controller = null;
        }
    }

    function cancel() {
        if (disposed || state.phase !== "submitting") return;
        generation += 1;
        controller?.abort();
        controller = null;
        publish({ phase: "uncertain", scope: state.scope });
    }

    return {
        getState: () => state,
        select,
        submit,
        cancel,
        subscribe(listener: () => void) {
            if (disposed) return () => {};
            listeners.add(listener);
            return () => { listeners.delete(listener); };
        },
        dispose() {
            cancel();
            disposed = true;
            generation += 1;
            controller?.abort();
            controller = null;
            listeners.clear();
        },
    };
}
