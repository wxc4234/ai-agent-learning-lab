import ChatPanel from "@/features/chat/components/chat-panel";
import AuthGate from "@/features/auth/components/auth-gate";

export default function HomePage() {
    return (
        <AuthGate>
            <ChatPanel />
        </AuthGate>
    );
}