import { isLocalMode, localHeaders } from "./runtime.ts";
import { isProposalIdentifier } from "../../../features/workbench/file-edit-proposal-data.ts";

export type GrantOperation = "read" | "issue" | "revoke";
const INVALID = "invalid_project_write_grant_input";
const UNCERTAIN = "project_write_grant_uncertain";
const READ_FAILED = "project_write_grant_read_failed";
const MESSAGES: Record<string, string> = {
    [INVALID]: "许可请求参数不符合要求",
    [UNCERTAIN]: "许可变更结果未确认，请先查询，勿重复提交",
    [READ_FAILED]: "许可读取失败，请重新查询",
    local_mode_required: "任务功能仅支持本地模式",
    local_access_rejected: "本地服务请求被拒绝，请检查运行配置",
    workspace_origin_rejected: "请求来源不被允许",
    unsupported_workspace_content_type: "许可请求必须使用application/json",
    workspace_not_accessible: "工作空间不存在或不可访问",
    project_write_grant_conflict: "许可记录或修订冲突，请重新查询",
    invalid_login_session: "身份无效，请检查本地运行配置",
    invalid_workspace_request: "许可请求无法解析",
    project_write_grant_not_forwarded: "本次请求尚未转发许可操作",
    project_write_grant_method_not_allowed: "请求方法不被允许",
};
const ERRORS: Record<number, readonly string[]> = {
    400: ["invalid_workspace_request"],
    401: ["invalid_login_session"],
    403: ["local_mode_required", "local_access_rejected", "workspace_origin_rejected"],
    404: ["workspace_not_accessible"],
    415: ["unsupported_workspace_content_type"],
    422: [INVALID],
};
function error(status: number, code: string): Response {
    return Response.json({ code, message: MESSAGES[code] }, {
        status, headers: { "Cache-Control": "no-store" },
    });
}
function record(value: unknown): value is Record<string, unknown> {
    return value !== null && typeof value === "object" && !Array.isArray(value);
}
function exact(value: Record<string, unknown>, keys: string[]): boolean {
    return Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
}

// 只用安全整数传递修订，禁止JS精度损失后撤销另一修订；当前记录仅1/2。
function revokeBody(value: unknown): { grant_id: string; revision: number } | null {
    if (!record(value) || !exact(value, ["grant_id", "revision"])
        || typeof value.grant_id !== "string" || !isProposalIdentifier(value.grant_id)
        || typeof value.revision !== "number" || !Number.isSafeInteger(value.revision) || value.revision <= 0) return null;
    return { grant_id: value.grant_id, revision: value.revision };
}

export async function projectWriteGrantProxy(
    request: Request, workspaceId: string, taskId: string, proposalId: string, operation: GrantOperation,
): Promise<Response> {
    const writing = operation !== "read";
    const failureCode = writing ? UNCERTAIN : READ_FAILED;
    if (!["read", "issue", "revoke"].includes(operation)
        || request.method !== (writing ? "POST" : "GET")) {
        return error(405, "project_write_grant_method_not_allowed");
    }
    let headers: Record<string, string>;
    try {
        if (!isLocalMode()) return error(403, "local_mode_required");
        // 仅使用服务器配置的token，不透传浏览器身份/凭证头。
        headers = localHeaders(request);
    } catch {
        return error(403, "local_access_rejected");
    }
    const origin = request.headers.get("origin");
    if (writing && !origin) return error(403, "workspace_origin_rejected");
    if (![workspaceId, taskId, proposalId].every(isProposalIdentifier)
        || new URL(request.url).searchParams.size !== 0) return error(422, INVALID);
    if (!writing && request.body !== null) return error(422, INVALID);
    if (writing && request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") {
        return error(415, "unsupported_workspace_content_type");
    }
    const cancelledBeforeForward = () => error(499, writing ? "project_write_grant_not_forwarded" : READ_FAILED);
    if (request.signal.aborted) return cancelledBeforeForward();
    let body: { action: "grant" } | { grant_id: string; revision: number } | undefined;
    if (writing) {
        try {
            const raw: unknown = await request.json();
            if (request.signal.aborted) return cancelledBeforeForward();
            if (operation === "issue") {
                if (!record(raw) || !exact(raw, ["action"]) || raw.action !== "grant") return error(422, INVALID);
                body = { action: "grant" };
            } else {
                const parsed = revokeBody(raw);
                if (!parsed) return error(422, INVALID);
                body = parsed;
            }
        } catch {
            return request.signal.aborted ? cancelledBeforeForward() : error(422, INVALID);
        }
    }
    // 预算覆盖fetch及上游正文读取，取消fetch不能撤销数据库提交。
    const timeout = AbortSignal.timeout(20_000);
    const signal = AbortSignal.any([request.signal, timeout]);
    let started = false;
    try {
        signal.throwIfAborted();
        started = true;
        const response = await fetch(
            `${process.env.API_BASE_URL ?? "http://127.0.0.1:8000"}/workspaces/${workspaceId}/tasks/${taskId}`
            + `/file-edit-proposals/${proposalId}/write-grant${operation === "revoke" ? "/revoke" : ""}`,
            {
                method: writing ? "POST" : "GET",
                headers: writing ? { ...headers, Origin: origin!, "Content-Type": "application/json" } : headers,
                ...(writing ? { body: JSON.stringify(body) } : {}),
                signal, cache: "no-store", redirect: "error",
            },
        );
        signal.throwIfAborted();
        const success = operation === "issue" ? 201 : 200;
        const allowed = response.status === 500 ? [failureCode]
            : response.status === 409 && writing ? ["project_write_grant_conflict"]
                : ERRORS[response.status];
        if (response.status !== success && !allowed) {
            await response.body?.cancel();
            signal.throwIfAborted();
            return error(502, failureCode);
        }
        const raw: unknown = await response.json();
        signal.throwIfAborted();
        if (response.status !== success) {
            if (record(raw) && typeof raw.code === "string" && allowed?.includes(raw.code)) {
                return error(response.status, raw.code);
            }
            return error(502, failureCode);
        }
        if (!record(raw) || raw.workspace_id !== workspaceId || raw.task_id !== taskId || raw.proposal_id !== proposalId) {
            return error(502, failureCode);
        }
        const grant = raw.grant;
        let publicGrant: { grant_id: string; revision: number; status: "enabled" | "revoked" } | null;
        if (grant === null && !writing) {
            publicGrant = null;
        } else {
            if (!record(grant) || typeof grant.grant_id !== "string" || !isProposalIdentifier(grant.grant_id)
                || !((grant.revision === 1 && grant.status === "enabled") || (grant.revision === 2 && grant.status === "revoked"))) {
                return error(502, failureCode);
            }
            if (operation === "issue" && grant.status !== "enabled") return error(502, failureCode);
            if (operation === "revoke" && (grant.status !== "revoked" || !body || !("grant_id" in body)
                || grant.grant_id !== body.grant_id || body.revision !== 1)) return error(502, failureCode);
            publicGrant = { grant_id: grant.grant_id, revision: grant.revision, status: grant.status };
        }
        // 重建公开字段，不透传target、路径、正文、Set-Cookie或上游响应头。
        return Response.json({ workspace_id: workspaceId, task_id: taskId, proposal_id: proposalId, grant: publicGrant }, {
            status: success, headers: { "Cache-Control": "no-store" },
        });
    } catch {
        if (!started && request.signal.aborted) return cancelledBeforeForward();
        return error(request.signal.aborted ? 499 : timeout.aborted ? 504 : 502, failureCode);
    }
}
