export type WorkspaceListItem = {
    external_id: string;
    name: string;
    created_at: string;
};

export type WorkspaceListData = {
    items: WorkspaceListItem[];
    has_more: boolean;
};

function isRecord(value: unknown): value is Record<string, unknown> {
    return (
        typeof value === "object" &&
        value !== null &&
        !Array.isArray(value)
    );
}

export function readWorkspaceList(
    value: unknown,
    limit: number,
): WorkspaceListData | null {
    if (
        !isRecord(value) ||
        !Array.isArray(value.items) ||
        typeof value.has_more !== "boolean" ||
        value.items.length > limit
    ) {
        return null;
    }

    // 后端多查询一条判断 has_more；有更多记录时，本页应达到请求数量。
    if (value.has_more && value.items.length !== limit) {
        return null;
    }

    const items: WorkspaceListItem[] = [];
    const identifiers = new Set<string>();

    for (const item of value.items) {
        if (
            !isRecord(item) ||
            typeof item.external_id !== "string" ||
            !/^[a-f0-9]{32}$/.test(item.external_id) ||
            typeof item.name !== "string" ||
            Array.from(item.name).length < 1 ||
            Array.from(item.name).length > 100 ||
            typeof item.created_at !== "string" ||
            !Number.isFinite(Date.parse(item.created_at)) ||
            identifiers.has(item.external_id)
        ) {
            return null;
        }

        identifiers.add(item.external_id);

        // 重建公开字段，避免把后端额外字段带入页面。
        items.push({
            external_id: item.external_id,
            name: item.name,
            created_at: item.created_at,
        });
    }

    return {
        items,
        has_more: value.has_more,
    };
}
