import AdminPanel from "@/components/AdminPanel";
import RequireRole from "@/components/RequireRole";

export default function AdminPage() {
  return (
    <RequireRole roles={["admin"]}>
      <AdminPanel />
    </RequireRole>
  );
}
