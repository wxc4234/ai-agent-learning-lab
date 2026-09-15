import { redirect } from "next/navigation";
import AccountPage from "@/features/auth/components/account-page";
import { isLocalMode } from "../api/_shared/runtime";

export const dynamic = "force-dynamic";

export default function LoginPage() {
    if (isLocalMode()) redirect("/");
    return <AccountPage />;
}
