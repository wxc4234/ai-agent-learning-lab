import AuthGate from "@/features/auth/components/auth-gate";
import CreateWorkspace from "@/features/workspaces/components/create-workspace";
import { isLocalMode } from "../../api/_shared/runtime";

export const dynamic = "force-dynamic";

export default function WorkspaceCreatePage() {
    const localMode = isLocalMode();
    return (
        <AuthGate localMode={localMode} returnTo="/workspaces/new">
            <CreateWorkspace localMode={localMode} />
        </AuthGate>
    );
}
