import AdminChatHistoryPanel from "@/components/AdminChatHistoryPanel";
import RequireRole from "@/components/RequireRole";
import { serverTranslator } from "@/i18n/server";

export async function generateMetadata() {
  const { t } = await serverTranslator();
  return { title: `${t("chat.historyAdminTitle")} — OKF Knowledge Service` };
}

export default function AdminChatHistoryPage() {
  return (
    <RequireRole roles={["security", "admin"]}>
      <AdminChatHistoryPanel />
    </RequireRole>
  );
}
