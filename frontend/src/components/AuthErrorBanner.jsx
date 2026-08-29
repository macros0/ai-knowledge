"use client";

import { useEffect, useState } from "react";

const MESSAGES = {
  session_expired: "Сессия входа истекла. Войдите снова.",
  unavailable: "Сервис входа временно недоступен. Попробуйте позже.",
};

/**
 * Баннер ошибок входа. Бэкенд редиректит на `/?auth_error=<code>` при сбое
 * OAuth-флоу (Keycloak): здесь код превращается в понятное сообщение.
 */
export default function AuthErrorBanner() {
  const [error, setError] = useState(null);

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
        Войти снова
      </a>
    </div>
  );
}
