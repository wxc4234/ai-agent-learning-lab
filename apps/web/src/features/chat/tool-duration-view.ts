// 把 ToolActivity 中的三态耗时转换成 UI 展示文本。
export function formatToolDuration(
	durationMs: number | null | undefined,
): string | null {
	// 工具仍在运行，尚未产生最终耗时，组件不渲染。
	if (durationMs === undefined) {
		return null;
	}

	// 工具在参数校验或注册检查阶段失败，没有真正执行。
	if (durationMs === null) {
		return "未进入执行阶段";
	}

	// 这里不能使用 truthy 判断，否则真实的 0 ms 会被隐藏。
	return `${durationMs} ms`;
}
