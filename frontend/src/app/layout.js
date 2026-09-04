import Link from "next/link";
import Script from "next/script";
import "./globals.css";
import Nav from "@/components/Nav";
import { ChatProvider } from "@/context/ChatContext";
import { ToastProvider } from "@/components/Toast";
import { AuthProvider } from "@/context/AuthContext";
import { ThemeProvider } from "@/context/ThemeContext";
import AuthBar from "@/components/AuthBar";
import ThemeToggle from "@/components/ThemeToggle";
import RequireAuth from "@/components/RequireAuth";
import AuthErrorBanner from "@/components/AuthErrorBanner";
import HealthBanner from "@/components/HealthBanner";
import { bootScript } from "@/lib/theme";

export const metadata = {
  title: "OKF Knowledge Service",
  description: "Загрузка документов и умный поиск по базе знаний",
  icons: { icon: "/icon.png" },
};

export default function RootLayout({ children }) {
  return (
    <html lang="ru" suppressHydrationWarning>
      <body>
        {/* Применяет сохранённую/системную тему до первой отрисовки (без мигания).
            beforeInteractive = инжектится в <head> до подключения стилей. */}
        <Script
          id="theme-boot"
          strategy="beforeInteractive"
          dangerouslySetInnerHTML={{ __html: bootScript() }}
        />
        <ThemeProvider>
          <AuthProvider>
            <ToastProvider>
              <ChatProvider>
                <div className="app-shell">
                  <header className="topbar">
                    <Link href="/" className="brand">
                      <img src="/icon.png" alt="" className="brand-logo" width="32" height="32" />
                      <h1>OKF Knowledge Service</h1>
                    </Link>
                    <Nav />
                    <AuthBar />
                    <ThemeToggle />
                  </header>
                  <HealthBanner />
                  <main>
                    <AuthErrorBanner />
                    <RequireAuth>{children}</RequireAuth>
                  </main>
                </div>
              </ChatProvider>
            </ToastProvider>
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
