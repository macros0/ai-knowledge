import SecurityPanel from "@/components/SecurityPanel";
import RequireRole from "@/components/RequireRole";

export default function SecurityPage() {
  return (
    <RequireRole roles={["security"]}>
      <SecurityPanel />
    </RequireRole>
  );
}
