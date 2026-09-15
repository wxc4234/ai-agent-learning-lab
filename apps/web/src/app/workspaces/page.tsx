import AuthGate from "@/features/auth/components/auth-gate";
import WorkspaceListPanel from "@/features/workspaces/components/workspace-list-panel";
import { isLocalMode } from "../api/_shared/runtime";

export const dynamic = "force-dynamic";

export default function WorkspacesPage() {
    const localMode = isLocalMode();

    return (
        <AuthGate localMode={localMode} returnTo="/workspaces">
            <WorkspaceListPanel localMode={localMode} />
        </AuthGate>
    );
}
