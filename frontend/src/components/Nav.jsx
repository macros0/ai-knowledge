"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/context/AuthContext";
import { useI18n } from "@/i18n/LocaleContext";

export default function Nav() {
  const pathname = usePathname();
  const { mode, hasRole } = useAuth();
  const { t } = useI18n();

  const baseItems = [
    { href: "/", label: t("nav.documents"), exact: true },
    { href: "/chat", label: t("nav.chat"), exact: false },
    { href: "/chat/history", label: t("nav.history"), exact: false },
  ];

  const roleItems = [
    { href: "/developments", label: t("nav.developments"), exact: false, roles: ["editor", "admin"] },
    { href: "/admin", label: t("nav.admin"), exact: true, roles: ["admin"] },
    { href: "/admin/languages", label: t("nav.adminLanguages"), exact: false, roles: ["admin"] },
    { href: "/security", label: t("nav.security"), exact: false, roles: ["security"] },
    { href: "/chat/history/admin", label: t("nav.historyAdmin"), exact: false, roles: ["security", "admin"] },
  ];

  const items = [
    ...baseItems,
    ...(mode === "disabled"
      ? roleItems
      : roleItems.filter((it) => hasRole(...it.roles))),
  ];

  return (
    <nav>
      {items.map((it) => {
        const active = it.exact ? pathname === it.href : pathname.startsWith(it.href);
        return (
          <Link key={it.href} href={it.href} className={`tab${active ? " active" : ""}`}>
            {it.label}
          </Link>
        );
      })}
    </nav>
  );
}