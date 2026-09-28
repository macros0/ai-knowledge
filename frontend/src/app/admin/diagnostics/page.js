import DiagnosticsPanel from "@/components/DiagnosticsPanel";
import RequireRole from "@/components/RequireRole";

export default function DiagnosticsPage() {
  return <RequireRole roles={["admin"]}><DiagnosticsPanel /></RequireRole>;
}
