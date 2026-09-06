import LanguagesPanel from "@/components/LanguagesPanel";
import RequireRole from "@/components/RequireRole";

export default function AdminLanguagesPage() {
  return (
    <RequireRole roles={["admin"]}>
      <LanguagesPanel />
    </RequireRole>
  );
}
