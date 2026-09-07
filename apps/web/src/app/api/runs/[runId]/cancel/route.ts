const API_BASE_URL = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";

export async function POST(
    request: Request,
    context: RouteContext<"/api/runs/[runId]/cancel">
) {
    const {runId} = await context.params;
    const requestBody = await request.json();

    let backendResponse: Response;
    try {
        backendResponse = await fetch(
			`${API_BASE_URL}/runs/${encodeURIComponent(runId)}/cancel`,
			{
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(requestBody),
				cache: "no-store",
			},
		);
    }
    catch {
        return new Response("后端服务暂时不可用", { status: 502 });
    }

    if (backendResponse.status === 204) {
		return new Response(null, { status: 204 });
	}

	return new Response(await backendResponse.text(), {
		status: backendResponse.status,
		headers: { "Content-Type": "text/plain; charset=utf-8" },
	});
}