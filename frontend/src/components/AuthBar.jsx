"use client";

import { useAuth } from "@/context/AuthContext";
import { useI18n } from "@/i18n/LocaleContext";

const ROLE_LABELS = {
  viewer: "Viewer",
  editor: "Editor",
  admin: "Admin",
  security: "Security",
};

export default function AuthBar() {
  const { user, mode, simUsers, login, logout, loading } = useAuth();
  const { t } = useI18n();

  // Пока /auth/me не ответил (mode=null), ничего не рендерим — иначе селектор
  // демо-юзеров мигает на каждой загрузке страницы.
  if (loading) return null;
  // В disabled-режиме авторизация выключена — панель не нужна.
  if (mode === "disabled") return null;
  if (mode === "sso" && user && user.user_id !== "anonymous") {
    // В sso-режиме роль и логин приходят с Keycloak.
    return (
      <div className="authbar">
        <span className="auth-username">{user.username}</span>
        <span className="auth-role">
          {ROLE_LABELS[user.roles?.[0]] ?? user.roles?.[0] ?? "—"}
        </span>
        <button onClick={logout}>{t("common.logout")}</button>
      </div>
    );
  }
  if (mode === "sso") {
    return (
      <div className="authbar">
        <span className="auth-mode-label">SSO</span>
        <a href="/api/auth/login" className="tab">
          {t("auth.corporateLogin")}
        </a>
      </div>
    );
  }

  // simulation: не залогинен — селектор демо-юзеров.
  if (!user || user.user_id === "anonymous") {
    return (
      <div className="authbar">
        <span className="auth-mode-label">{mode}</span>
        <select
          className="auth-select"
          defaultValue=""
          onChange={(e) => e.target.value && login(e.target.value)}
        >
          <option value="" disabled>
            {t("auth.loginAs")}
          </option>
          {(simUsers ?? []).map((u) => (
            <option key={u.username} value={u.username}>
              {u.username} · {ROLE_LABELS[u.roles?.[0]] ?? "—"}
            </option>
          ))}
        </select>
      </div>
    );
  }

  // simulation: залогинен.
  return (
    <div className="authbar">
      <span className="auth-username">{user.username}</span>
      <span className="auth-role">
        {ROLE_LABELS[user.roles?.[0]] ?? user.roles?.[0] ?? "—"}
      </span>
      <button onClick={logout}>{t("common.logout")}</button>
    </div>
  );
}