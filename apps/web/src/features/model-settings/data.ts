export type ModelChannel = {
    enabled: boolean; base_url: string; model: string; dimensions: number | null;
    request_dimensions: boolean; key_configured: boolean; source: "local" | "environment";
};
export type ModelSettings = { revision: string; chat: ModelChannel; embedding: ModelChannel };
export function readSettings(value: unknown): ModelSettings | null {
    if (!value || typeof value !== "object") return null;
    const raw = value as Record<string, unknown>;
    if (Object.keys(raw).sort().join() !== "chat,embedding,revision" || typeof raw.revision !== "string" || !/^[a-f0-9]{64}$/.test(raw.revision)) return null;
    function channel(value: unknown): ModelChannel | null {
        if (!value || typeof value !== "object") return null;
        const c = value as Record<string, unknown>;
        if (Object.keys(c).sort().join() !== "base_url,dimensions,enabled,key_configured,model,request_dimensions,source"
            || typeof c.enabled !== "boolean" || typeof c.key_configured !== "boolean" || typeof c.request_dimensions !== "boolean"
            || typeof c.base_url !== "string" || c.base_url.length > 2048 || typeof c.model !== "string" || c.model.length > 256
            || c.source !== "local" && c.source !== "environment"
            || c.dimensions !== null && (typeof c.dimensions !== "number" || !Number.isInteger(c.dimensions) || c.dimensions < 1 || c.dimensions > 4096)) return null;
        return c as ModelChannel;
    }
    const chat = channel(raw.chat), embedding = channel(raw.embedding);
    return chat && embedding ? { revision: raw.revision, chat, embedding } : null;
}
