import ChatHistoryPanel from "@/components/ChatHistoryPanel";
import { serverTranslator } from "@/i18n/server";

export async function generateMetadata() {
  const { t } = await serverTranslator();
  return { title: `${t("chat.historyTitle")} — OKF Knowledge Service` };
}

export default function ChatHistoryPage() {
  return <ChatHistoryPanel />;
}
