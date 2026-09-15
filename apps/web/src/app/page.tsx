import { isLocalMode } from "./api/_shared/runtime";
import ChatPanel from "@/features/chat/components/chat-panel";
import AuthGate from "@/features/auth/components/auth-gate";

export const dynamic = "force-dynamic";

export default function HomePage() {
    // 本地首页直接使用三栏工作台，不再叠加临时顶部导航。
    // 内部访问边界仍由 BFF 和 FastAPI 执行。
    if (isLocalMode()) {
        return <ChatPanel />;
    }

    return (
        <AuthGate>
            <ChatPanel localMode={false} />
        </AuthGate>
    );
}
