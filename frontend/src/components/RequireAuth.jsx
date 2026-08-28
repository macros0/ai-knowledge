"use client";

import { useAuth } from "@/context/AuthContext";

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

  if (loading) return null;
  if (mode === "disabled") return children;
  if (user && user.user_id !== "anonymous") return children;

  // Нет сессии — экран входа вместо контента.
  return (
    <section className="panel auth-gate">
      <div className="auth-gate-card">
        <h2>Вход в систему</h2>
        <p className="muted">
          {mode === "sso"
            ? "Для работы с базой знаний войдите через корпоративный вход."
            : "Выберите тестового пользователя для работы с базой знаний."}
        </p>
        {mode === "sso" ? (
          <a href="/api/auth/login" className="tab auth-gate-login">
            Войти через корпоративный вход
          </a>
        ) : (
          <select
            className="auth-select"
            defaultValue=""
            onChange={(e) => e.target.value && login(e.target.value)}
          >
            <option value="" disabled>
              Войти как…
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