import ChatPanel from "@/components/ChatPanel";
import { serverTranslator } from "@/i18n/server";

export async function generateMetadata() {
  const { t } = await serverTranslator();
  return { title: `${t("chat.title")} — OKF Knowledge Service` };
}

export default function ChatPage() {
  return <ChatPanel />;
}
