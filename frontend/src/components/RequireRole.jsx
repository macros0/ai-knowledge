"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/context/AuthContext";

/**
 * Гейт страницы по роли (route-level): пускает только обладателя нужной роли.
 *
 * ВАЖНО: пока идёт первый запрос профиля (loading === true), роль ещё не
 * известна — НЕ принимаем решение о редиректе по undefined-роли (иначе
 * admin/security выкидывались бы со своих страниц при F5). Ждём loading.
 *
 * В disabled-режиме (всё открыто) пропускаем всех — как и require_role на бэкенде.
 */
export default function RequireRole({ roles = [], children }) {
  const { user, mode, loading, hasRole } = useAuth();
  const router = useRouter();

  const allowed = mode === "disabled" || (user && hasRole(...roles));

  useEffect(() => {
    if (!loading && !allowed) {
      router.replace("/");
    }
  }, [loading, allowed, router]);

  if (loading) return null;
  if (!allowed) return null;
  return children;
}
