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
    { href: "/admin/glossary", label: t("nav.adminGlossary"), exact: false, roles: ["editor", "admin"] },
    { href: "/security", label: t("nav.security"), exact: false, roles: ["security"] },
    { href: "/chat/history/admin", label: t("nav.historyAdmin"), exact: false, roles: ["security", "admin"] },
  ];

  const primaryRoleItems = roleItems.filter((it) => it.href === "/developments");
  const overflowRoleItems = roleItems.filter((it) => it.href !== "/developments");
  const items = [
    ...baseItems,
    ...(mode === "disabled" ? primaryRoleItems : primaryRoleItems.filter((it) => hasRole(...it.roles))),
  ];

  const overflowItems = mode === "disabled" ? overflowRoleItems : overflowRoleItems.filter((it) => hasRole(...it.roles));

  const renderItem = (it) => {
    const active = it.exact ? pathname === it.href : pathname.startsWith(it.href);
    return (
      <Link key={it.href} href={it.href} className={`tab${active ? " active" : ""}`}>
        {it.label}
      </Link>
    );
  };

  return (
    <nav>
      {items.map(renderItem)}
      {overflowItems.length > 0 && (
        <details className="nav-more">
          <summary className="tab">{t("nav.more")}</summary>
          <div className="nav-more-menu">{overflowItems.map(renderItem)}</div>
        </details>
      )}
    </nav>
  );
}
