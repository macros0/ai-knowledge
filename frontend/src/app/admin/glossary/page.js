import GlossaryPanel from "@/components/GlossaryPanel";
import RequireRole from "@/components/RequireRole";
import { serverTranslator } from "@/i18n/server";

export async function generateMetadata() {
  const { t } = await serverTranslator();
  return { title: `${t("admin.glossary.title")} — OKF Knowledge Service` };
}

export default function AdminGlossaryPage() {
  return <RequireRole roles={["editor", "admin"]}><GlossaryPanel /></RequireRole>;
}
