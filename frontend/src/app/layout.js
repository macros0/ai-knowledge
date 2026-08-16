import "./globals.css";
import Nav from "@/components/Nav";
import { ChatProvider } from "@/context/ChatContext";

export const metadata = {
  title: "OKF Knowledge Service",
  description: "Загрузка документов и умный поиск по базе знаний",
};

export default function RootLayout({ children }) {
  return (
    <html lang="ru">
      <body>
        <ChatProvider>
          <div className="app-shell">
            <header className="topbar">
              <h1>OKF Knowledge Service</h1>
              <Nav />
            </header>
            <main>{children}</main>
          </div>
        </ChatProvider>
      </body>
    </html>
  );
}
