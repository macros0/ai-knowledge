import Link from "next/link";
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
import { backendFetch } from "@/lib/backendFetch";
import InlineScript from "@/components/InlineScript";

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
  let initialOverrides = {};
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
  // Runtime-override UI-словаря (Этап 7 фаза C): SSR подгружает override текущей
  // локали, чтобы серверная и клиентская разметка совпадали (без hydration-mismatch).
  // При недоступности бэкенда/404 — fallback на versioned-словарь релиза.
  try {
    const res = await backendFetch(`/api/i18n/${ssrLocale}`);
    if (res.ok) {
      const body = await res.json();
      if (body && body.data) initialOverrides = { [ssrLocale]: body.data };
    }
  } catch {
    // override недоступен — не критично
  }

  return (
    <html lang={lang} suppressHydrationWarning>
      <body>
        {/* Применяет сохранённую/системную тему до первой отрисовки (без мигания).
            Hydration-safe inline script (text/javascript в SSR, text/plain на клиенте) —
            иначе React 19 ругается на <script> в дереве компонентов. */}
        <InlineScript html={bootScript()} />
        {/* No-JS/edge фолбэк для <html lang> (SSR уже выставил его из cookie/заголовка). */}
        <InlineScript html={localeBootScript()} />
        <LocaleProvider initialLocale={ssrLocale} initialOverrides={initialOverrides}>
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
