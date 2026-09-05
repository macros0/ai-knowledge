"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n/LocaleContext";

/**
 * Баннер ошибок входа. Бэкенд редиректит на `/?auth_error=<code>` при сбое
 * OAuth-флоу (Keycloak): здесь код превращается в понятное сообщение.
 */
export default function AuthErrorBanner() {
  const { t } = useI18n();
  const [error, setError] = useState(null);

  const MESSAGES = {
    session_expired: t("authError.sessionExpired"),
    unavailable: t("authError.unavailable"),
  };

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setError(params.get("auth_error"));
    // Убираем auth_error из URL, чтобы баннер не «залипал» при reload/переоткрытии
    // вкладки со старым query-параметром.
    if (params.has("auth_error")) {
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  if (!error || !MESSAGES[error]) return null;

  return (
    <div className="auth-error-banner" role="alert">
      <span>{MESSAGES[error]}</span>
      <a href="/api/auth/login" className="auth-error-retry">
        {t("common.loginAgain")}
      </a>
    </div>
  );
}
