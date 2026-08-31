import AdminChatHistoryPanel from "@/components/AdminChatHistoryPanel";
import RequireRole from "@/components/RequireRole";

export const metadata = {
  title: "История чата пользователей — OKF Knowledge Service",
};

export default function AdminChatHistoryPage() {
  return (
    <RequireRole roles={["security", "admin"]}>
      <AdminChatHistoryPanel />
    </RequireRole>
  );
}
