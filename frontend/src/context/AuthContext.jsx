"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  getMe,
  logoutAuth,
  simulateAuth,
} from "@/lib/api";
import { useI18n } from "@/i18n/LocaleContext";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  // null = ответ /auth/me ещё не получен (или запрос упал). Fail-closed: до
  // подтверждения режима сервером все проверки `mode === "disabled"` дают false
  // — UI рендерится с правами guest, а не «всё открыто» (раньше упавший /auth/me
  // молча оставлял mode="disabled" → полный editor/admin UI анониму).
  const [mode, setMode] = useState(null);
  const [simUsers, setSimUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [authError, setAuthError] = useState(null);
  const { t } = useI18n();

  const apply = useCallback((data) => {
    setUser(data?.user ?? null);
    setMode(data?.mode ?? "disabled");
    setSimUsers(data?.sim_users ?? []);
    setAuthError(null);
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      apply(await getMe());
    } catch {
      setAuthError("auth.serverUnreachable");
    } finally {
      setLoading(false);
    }
  }, [apply]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const login = useCallback(
    async (username) => {
      setLoading(true);
      try {
        apply(await simulateAuth(username));
      } finally {
        setLoading(false);
      }
    },
    [apply]
  );

  const logout = useCallback(() => logoutAuth(), []);

  const role = user?.roles?.[0] ?? null;

  const hasRole = useCallback(
    (...roles) => {
      if (!user) return false;
      const userRoles = user.roles ?? [];
      return roles.some((r) => userRoles.includes(r));
    },
    [user]
  );

  const value = useMemo(
    () => ({ user, mode, simUsers, loading, login, logout, refresh, role, hasRole }),
    [user, mode, simUsers, loading, login, logout, refresh, role, hasRole]
  );

  // Fail-closed гейт: /auth/me упал и пользователя нет — не рендерим приложение
  // «наугад» (полный UI с рольями из дефолтного mode), а предлагаем повторить.
  if (authError && !user) {
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: "16px",
          fontFamily: "inherit",
        }}
      >
        <p>{t(authError)}</p>
        <button type="button" className="btn" onClick={refresh}>
          {t("common.retry")}
        </button>
      </div>
    );
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
