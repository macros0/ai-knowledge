"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/context/AuthContext";

const baseItems = [
  { href: "/", label: "Документы", exact: true },
  { href: "/chat", label: "Вопросы к документам", exact: false },
  { href: "/chat/history", label: "История", exact: false },
];

const roleItems = [
  { href: "/developments", label: "Разработки", exact: false, roles: ["editor", "admin"] },
  { href: "/admin", label: "Системные операции", exact: false, roles: ["admin"] },
  { href: "/security", label: "Аудит", exact: false, roles: ["security"] },
  { href: "/chat/history/admin", label: "История пользователей", exact: false, roles: ["security", "admin"] },
];

export default function Nav() {
  const pathname = usePathname();
  const { mode, hasRole } = useAuth();

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
