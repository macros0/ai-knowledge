"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const items = [
  { href: "/", label: "Документы", exact: true },
  { href: "/chat", label: "Чат", exact: false },
];

export default function Nav() {
  const pathname = usePathname();
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
