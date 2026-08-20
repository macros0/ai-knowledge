import Link from "next/link";
import "./globals.css";
import Nav from "@/components/Nav";
import { ChatProvider } from "@/context/ChatContext";
import { ToastProvider } from "@/components/Toast";
import HealthBanner from "@/components/HealthBanner";

export const metadata = {
  title: "OKF Knowledge Service",
  description: "Загрузка документов и умный поиск по базе знаний",
};

export default function RootLayout({ children }) {
  return (
    <html lang="ru">
      <body>
        <ToastProvider>
          <ChatProvider>
            <div className="app-shell">
              <header className="topbar">
                <Link href="/" className="brand">
                  <h1>OKF Knowledge Service</h1>
                </Link>
                <Nav />
              </header>
              <HealthBanner />
              <main>{children}</main>
            </div>
          </ChatProvider>
        </ToastProvider>
      </body>
    </html>
  );
}
