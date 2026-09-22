import { isAbsolute } from "node:path";
import { isLocalMode, localHeaders } from "../../../../_shared/runtime.ts";

export const runtime = "nodejs";
const ERRORS: Record<string, [number, string]> = {
    proposal_application_busy: [409, "存在执行中或结果未确认的文件应用，暂不能变更资源"],
    local_mode_required: [403, "项目目录功能仅支持本地模式"],
    local_access_rejected: [403, "本地服务拒绝访问，请检查运行配置"],
    workspace_origin_rejected: [403, "请求来源不被允许"],
    workspace_not_accessible: [404, "工作空间不存在或不可访问"],
    workspace_already_bound: [409, "项目已绑定其他目录，请重新读取状态"],
    directory_picker_busy: [409, "已有目录选择窗口，请先完成或取消选择"],
    directory_picker_timeout: [408, "目录选择已超时，请重新选择"],
    directory_picker_unsupported: [501, "当前系统暂不支持目录选择"],
    directory_picker_unavailable: [503, "无法打开系统目录选择窗口，请检查本地桌面环境"],
    invalid_workspace_input: [422, "请求参数不符合要求"],
    invalid_directory_path: [422, "所选目录路径不符合要求"],
    directory_not_found: [422, "所选目录已经不存在，请重新选择"],
    directory_access_denied: [422, "没有权限访问所选目录"],
    not_a_directory: [422, "请选择文件夹"],
    root_directory_not_allowed: [422, "不能选择文件系统根目录"],
    directory_unavailable: [503, "暂时无法访问所选目录"],
    workspace_binding_failed: [500, "目录选择结果未确认，请重新读取状态"],
};

function error(status: number, code: string, message: string) {
    return Response.json({ code, message }, { status, headers: { "Cache-Control": "no-store" } });
}
function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

export async function POST(request: Request, context: { params: Promise<{ workspaceId: string }> }) {
    let headers: Record<string, string>;
    try {
        if (!isLocalMode()) return error(403, "local_mode_required", "项目目录功能仅支持本地模式");
        headers = localHeaders(request);
    } catch {
        return error(403, "local_access_rejected", "本地服务请求被拒绝");
    }
    // 用户主动点击才发送 POST，要求可信 Origin 和严格空 JSON 正文。
    const origin = request.headers.get("origin");
    if (!origin) return error(403, "workspace_origin_rejected", "请求来源不被允许");
    if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") {
        return error(415, "unsupported_workspace_content_type", "请求必须使用 JSON");
    }
    const { workspaceId } = await context.params;
    if (!/^[a-f0-9]{32}$/.test(workspaceId)) return error(422, "invalid_workspace_input", "工作空间标识不符合要求");
    // 系统窗口允许等待 120 秒，代理留出返回与保存时间，不沿用普通 GET 的 10 秒。
    const signal = AbortSignal.any([request.signal, AbortSignal.timeout(130_000)]);
    try {
        signal.throwIfAborted();
        const body: unknown = await request.json();
        if (!isRecord(body) || Object.keys(body).length !== 0) return error(422, "invalid_workspace_input", "选择目录不接受额外参数");
        signal.throwIfAborted();
        const response = await fetch(`${process.env.API_BASE_URL ?? "http://127.0.0.1:8000"}/workspaces/${workspaceId}/directory/select`, {
            method: "POST",
            headers: { ...headers, Origin: origin, "Content-Type": "application/json" },
            body: "{}", signal, cache: "no-store", redirect: "error",
        });
        signal.throwIfAborted();
        // 用户取消选择是正常结果，不解析空正文，也不误报为失败。
        if (response.status === 204) return new Response(null, { status: 204, headers: { "Cache-Control": "no-store" } });
        const payload: unknown = await response.json();
        signal.throwIfAborted();
        if (isRecord(payload)) {
            if (response.status !== 200 && typeof payload.code === "string" && Object.hasOwn(ERRORS, payload.code)) {
                const [status, message] = ERRORS[payload.code];
                if (response.status === status) return error(status, payload.code, message);
            }
            if (response.status === 200 && payload.external_id === workspaceId &&
                typeof payload.name === "string" && Array.from(payload.name).length >= 1 && Array.from(payload.name).length <= 100 &&
                typeof payload.root_path === "string" && !payload.root_path.includes("\0") && isAbsolute(payload.root_path)) {
                return Response.json({ external_id: workspaceId, name: payload.name, root_path: payload.root_path }, { headers: { "Cache-Control": "no-store" } });
            }
        }
    } catch {
        // 断开或超时并不证明服务端没有保存，禁止自动重发选择请求。
        if (request.signal.aborted) return error(499, "directory_selection_uncertain", "目录选择结果未确认，请重新读取状态");
    }
    return error(502, "directory_selection_uncertain", "目录选择结果未确认，请重新读取状态");
}
