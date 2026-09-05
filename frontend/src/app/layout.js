import Link from "next/link";
import Script from "next/script";
import { cookies, headers } from "next/headers";
import "./globals.css";
import Nav from "@/components/Nav";
import { ChatProvider } from "@/context/ChatContext";
import { ToastProvider } from "@/components/Toast";
import { AuthProvider } from "@/context/AuthContext";
import { ThemeProvider } from "@/context/ThemeContext";
import { LocaleProvider } from "@/i18n/LocaleContext";
import AuthBar from "@/components/AuthBar";
import ThemeToggle from "@/components/ThemeToggle";
import LocaleToggle from "@/components/LocaleToggle";
import RequireAuth from "@/components/RequireAuth";
import AuthErrorBanner from "@/components/AuthErrorBanner";
import HealthBanner from "@/components/HealthBanner";
import { bootScript } from "@/lib/theme";
import { bootScript as localeBootScript } from "@/i18n/boot";
import { resolveServerLocale } from "@/i18n/core";

export const metadata = {
  title: "OKF Knowledge Service",
  description: "Загрузка документов и умный поиск по базе знаний",
  icons: { icon: "/icon.png" },
};

export default async function RootLayout({ children }) {
  // SSR-локаль: cookie (сохранённый выбор) → Accept-Language (первый заход) → ru.
  // Чтение cookies()/headers() переводит layout в dynamic-рендеринг — осознанное
  // решение: без этого SSR-разметка (Nav, заголовки) расходилась бы с языком
  // клиента и давала hydration-mismatch. Для внутреннего инструмента за
  // авторизацией это приемлемо.
  let ssrLocale = "ru";
  let lang = "ru";
  try {
    const cookieStore = await cookies();
    const headerStore = await headers();
    const cookieLocale = cookieStore.get("okf.locale")?.value;
    const accept = headerStore.get("accept-language");
    ssrLocale = resolveServerLocale(cookieLocale, accept);
    lang = ssrLocale;
  } catch {
    // headers/cookies недоступны (пререндер) — фолбэк на дефолтный язык.
  }

  return (
    <html lang={lang} suppressHydrationWarning>
      <body>
        {/* Применяет сохранённую/системную тему до первой отрисовки (без мигания).
            beforeInteractive = инжектится в <head> до подключения стилей. */}
        <Script
          id="theme-boot"
          strategy="beforeInteractive"
          dangerouslySetInnerHTML={{ __html: bootScript() }}
        />
        {/* No-JS/edge фолбэк для <html lang> (SSR уже выставил его из cookie/заголовка). */}
        <Script
          id="locale-boot"
          strategy="beforeInteractive"
          dangerouslySetInnerHTML={{ __html: localeBootScript() }}
        />
        <LocaleProvider initialLocale={ssrLocale}>
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
                      <LocaleToggle />
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
        </LocaleProvider>
      </body>
    </html>
  );
}
