const API_BASE_URL = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";

export async function POST(request: Request) {
	const requestBody = await request.json();

	let backendResponse: Response;
	try {
		backendResponse = await fetch(`${API_BASE_URL}/chat/stream`, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(requestBody),
			signal: request.signal,
			cache: "no-store",
		});
	} catch {
		return new Response("后端服务暂时不可用", { status: 502 });
	}

	if (!backendResponse.ok) {
		const errorBody = await backendResponse.text();

		return new Response(errorBody || "后端服务暂时不可用", {
			status: backendResponse.status,
			headers: { "Content-Type": "text/plain; charset=utf-8" },
		});
	}

	if (!backendResponse.body) {
		return new Response("后端没有返回流式内容", { status: 502 });
	}

	// 直接转发 ReadableStream；读取 json/text 会缓冲完整回答，破坏逐块显示。
	const responseHeaders = new Headers({
		"Content-Type":
			backendResponse.headers.get("content-type") ??
			"text/plain; charset=utf-8",
		"Cache-Control": "no-cache",
	});

	const runId = backendResponse.headers.get("x-run-id");

	if (runId) {
		responseHeaders.set("X-Run-ID", runId);
	}

	return new Response(backendResponse.body, {
		status: backendResponse.status,
		headers: responseHeaders,
	});
}
