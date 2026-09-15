// 本模块只能由服务端页面和 BFF 导入，凭证不能传入客户端组件。
export function isLocalMode(): boolean {
    const mode = process.env.APP_MODE ?? "account";
    if (mode !== "local" && mode !== "account") {
        throw new Error("Invalid APP_MODE");
    }
    return mode === "local";
}

export function localHeaders(request: Request): Record<string, string> {
    if (!isLocalMode()) return {};
    const loopback = new Set(["localhost", "127.0.0.1", "[::1]"]);
    const backend = new URL(process.env.API_BASE_URL ?? "http://127.0.0.1:8000");
    const origin = request.headers.get("origin");
    const allowed = (process.env.AUTH_ALLOWED_ORIGINS ?? "http://localhost:3000,http://127.0.0.1:3000").split(",").map(value => value.trim());
    const token = process.env.LOCAL_RUNTIME_TOKEN ?? "";
    // 不信任浏览器传来的内部凭证，也不向远程后端发送本机凭证。
    if (
        !loopback.has(new URL(request.url).hostname) ||
        !loopback.has(backend.hostname) || backend.protocol !== "http:" ||
        request.headers.get("sec-fetch-site") === "cross-site" ||
        (origin !== null && !allowed.includes(origin)) ||
        !/^[a-f0-9]{64}$/.test(token)
    ) {
        throw new Error("Local runtime access rejected");
    }
    return { "X-Local-Runtime-Token": token };
}
