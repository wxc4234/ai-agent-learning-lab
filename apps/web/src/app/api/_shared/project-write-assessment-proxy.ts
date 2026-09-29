import { isLocalMode, localHeaders } from "./runtime.ts";
import { isProposalIdentifier } from "../../../features/workbench/file-edit-proposal-data.ts";

const INVALID = "invalid_project_write_assessment_input";
const READ_FAILED = "project_write_assessment_read_failed";

// 与后端公开协议保持一致；未来新增分类必须经过显式审阅。
// eligible仅描述观察时基线匹配；真正应用使用独立写入入口。
const RESULTS = new Set<string>([
    "invalid_facts",
    "not_authorized",
    "apply_not_requested",
    "grant_missing",
    "grant_revoked",
    "grant_changed",
    "target_changed",
    "proposal_not_approved",
    "application_not_idle",
    "diff_incomplete",
    "baseline_changed",
    "candidate_changed",
    "filesystem_unconfirmed",
    "platform_unsupported",
    "exclusive_access_unconfirmed", "eligible",
]);

const MESSAGES: Record<string, string> = {
    [INVALID]: "前置检查参数不符合要求",
    [READ_FAILED]: "前置检查未取得可信结果，当前结果未知",
    local_mode_required: "任务功能仅支持本地模式",
    local_access_rejected: "本地服务请求被拒绝，请检查运行配置",
    workspace_origin_rejected: "请求来源不被允许",
    unsupported_workspace_content_type: "前置检查必须使用application/json",
    workspace_not_accessible: "工作空间不存在或不可访问",
    invalid_login_session: "身份无效，请检查本地运行配置",
    invalid_workspace_request: "前置检查请求无法解析",
    project_write_assessment_method_not_allowed: "请求方法不被允许",
};

// 同时限制状态码与错误码，不能只看到熟悉的code就直接透传。
const ERRORS: Partial<Record<number, readonly string[]>> = {
    400: ["invalid_workspace_request"],
    401: ["invalid_login_session"],
    403: [
        "local_mode_required",
        "local_access_rejected",
        "workspace_origin_rejected",
    ],
    404: ["workspace_not_accessible"],
    415: ["unsupported_workspace_content_type"],
    422: [INVALID],
    500: [READ_FAILED],
};

type AssessmentBody = {
    grant_id: string;
    revision: number;
    apply_requested: boolean;
};

function error(status: number, code: string): Response {
    // 错误文案由BFF生成，不携带上游异常、路径或内部凭证。
    return Response.json(
        { code, message: MESSAGES[code] },
        {
            status,
            headers: { "Cache-Control": "no-store" },
        },
    );
}

function record(value: unknown): value is Record<string, unknown> {
    return value !== null
        && typeof value === "object"
        && !Array.isArray(value);
}

function parseBody(value: unknown): AssessmentBody | null {
    if (!record(value)) return null;

    const keys = ["grant_id", "revision", "apply_requested"];

    if (
        Object.keys(value).length !== keys.length
        || !keys.every(key => Object.hasOwn(value, key))
        || typeof value.grant_id !== "string"
        || !isProposalIdentifier(value.grant_id)
        || typeof value.revision !== "number"
        || !Number.isSafeInteger(value.revision)
        || value.revision <= 0
        || typeof value.apply_requested !== "boolean"
    ) {
        return null;
    }

    // 重建请求，不把调用方提供的可信事实或额外字段送入后端。
    return {
        grant_id: value.grant_id,
        revision: value.revision,
        apply_requested: value.apply_requested,
    };
}

export async function projectWriteAssessmentProxy(
    request: Request,
    workspaceId: string,
    taskId: string,
    proposalId: string,
): Promise<Response> {
    if (request.method !== "POST") {
        return error(405, "project_write_assessment_method_not_allowed");
    }

    const url = new URL(request.url);
    const origin = request.headers.get("origin");

    // 本入口要求显式同源Origin；服务端门禁继续检查配置白名单。
    if (!origin || origin !== url.origin) {
        return error(403, "workspace_origin_rejected");
    }

    let headers: Record<string, string>;

    try {
        if (!isLocalMode()) {
            return error(403, "local_mode_required");
        }

        // 仅注入服务端配置的本机令牌；不透传浏览器的凭证头。
        headers = localHeaders(request);
    } catch {
        return error(403, "local_access_rejected");
    }

    if (
        ![workspaceId, taskId, proposalId].every(isProposalIdentifier)
        || url.searchParams.size !== 0
    ) {
        return error(422, INVALID);
    }

    const contentType = request.headers.get("content-type")
        ?.split(";", 1)[0].trim().toLowerCase();

    if (contentType !== "application/json") {
        return error(415, "unsupported_workspace_content_type");
    }

    if (request.signal.aborted) {
        return error(499, READ_FAILED);
    }

    let body: AssessmentBody | null;

    try {
        const raw: unknown = await request.json();

        // 正文读取过程中发生取消，也不能继续转发。
        if (request.signal.aborted) {
            return error(499, READ_FAILED);
        }

        body = parseBody(raw);
    } catch {
        return request.signal.aborted
            ? error(499, READ_FAILED)
            : error(422, INVALID);
    }

    if (body === null) {
        return error(422, INVALID);
    }

    // 同一取消信号覆盖上游请求及响应正文读取。
    const timeout = AbortSignal.timeout(20_000);
    const signal = AbortSignal.any([request.signal, timeout]);

    try {
        signal.throwIfAborted();

        // BFF不持有数据库事务；后端负责只读检查的事务边界。
        // 此处只有一次请求，不重试，也不调用许可变更或应用接口。
        const response = await fetch(
            `${process.env.API_BASE_URL ?? "http://127.0.0.1:8000"}`
                + `/workspaces/${workspaceId}/tasks/${taskId}`
                + `/file-edit-proposals/${proposalId}/write-grant/assessment`,
            {
                method: "POST",
                headers: {
                    ...headers,
                    Origin: origin,
                    "Content-Type": "application/json",
                },
                body: JSON.stringify(body),
                signal,
                cache: "no-store",
                redirect: "error",
            },
        );

        signal.throwIfAborted();

        const allowedErrors = ERRORS[response.status];
        const responseType = response.headers.get("content-type")
            ?.split(";", 1)[0].trim().toLowerCase();

        if (
            (response.status !== 200 && !allowedErrors)
            || responseType !== "application/json"
        ) {
            await response.body?.cancel();
            signal.throwIfAborted();
            return error(502, READ_FAILED);
        }

        const raw: unknown = await response.json();
        signal.throwIfAborted();

        if (response.status !== 200) {
            if (
                record(raw)
                && typeof raw.code === "string"
                && allowedErrors?.includes(raw.code)
            ) {
                return error(response.status, raw.code);
            }

            return error(502, READ_FAILED);
        }

        // HTTP成功不代表响应可信：必须核对三个资源ID与固定分类。
        if (
            !record(raw)
            || raw.workspace_id !== workspaceId
            || raw.task_id !== taskId
            || raw.proposal_id !== proposalId
            || typeof raw.result !== "string"
            || !RESULTS.has(raw.result)
        ) {
            return error(502, READ_FAILED);
        }

        // 只投影公开字段，丢弃内部target、路径、凭证及上游响应头。
        return Response.json(
            {
                workspace_id: workspaceId,
                task_id: taskId,
                proposal_id: proposalId,
                result: raw.result,
            },
            {
                status: 200,
                headers: { "Cache-Control": "no-store" },
            },
        );
    } catch {
        // 所有传输失败都保留未知，不伪造grant_missing或可执行结果。
        const status = request.signal.aborted
            ? 499
            : timeout.aborted
                ? 504
                : 502;

        return error(status, READ_FAILED);
    }
}
