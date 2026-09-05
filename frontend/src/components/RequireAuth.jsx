"use client";

import { useAuth } from "@/context/AuthContext";
import { useI18n } from "@/i18n/LocaleContext";

const ROLE_LABELS = {
  viewer: "Viewer",
  editor: "Editor",
  admin: "Admin",
  security: "Security",
};

/**
 * Обёртка страниц: в режимах simulation/sso без сессии вместо контента
 * показывает экран входа. В disabled (всё открыто) рендерит children как есть.
 */
export default function RequireAuth({ children }) {
  const { user, mode, simUsers, login, loading } = useAuth();
  const { t } = useI18n();

  if (loading) return null;
  if (mode === "disabled") return children;
  if (user && user.user_id !== "anonymous") return children;

  // Нет сессии — экран входа вместо контента.
  return (
    <section className="panel auth-gate">
      <div className="auth-gate-card">
        <h2>{t("auth.gateTitle")}</h2>
        <p className="muted">
          {mode === "sso" ? t("auth.gateSsoHint") : t("auth.gateSimHint")}
        </p>
        {mode === "sso" ? (
          <a href="/api/auth/login" className="tab auth-gate-login">
            {t("auth.corporateLogin")}
          </a>
        ) : (
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
        )}
      </div>
    </section>
  );
}