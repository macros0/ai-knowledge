"use client";

import Link from "next/link";
import { inModelContext } from "./chatSourceContext.mjs";
import { documentHref, sourceHref } from "./chatSourceLinks.mjs";

// Превращает ссылки-цитаты [N] в remark-узлы link на якорь #cite-N.
function remarkCiteLinks() {
  function splitCites(node) {
    const parts = node.value.split(/(\[\d+\])/g);
    if (parts.length === 1) return [node];
    const out = [];
    for (const part of parts) {
      const m = /^\[(\d+)\]$/.exec(part);
      if (!m) {
        if (part) out.push({ type: node.type, value: part });
        continue;
      }
      const n = Number(m[1]);
      out.push({
        type: "link",
        url: `#cite-${n}`,
        children: [{ type: "text", value: `[${n}]` }],
      });
    }
    return out;
  }

  function transformChildren(parent) {
    if (!parent || !Array.isArray(parent.children)) return;
    const next = [];
    for (const child of parent.children) {
      if ((child.type === "text" || child.type === "inlineCode") && child.value) {
        const converted = splitCites(child);
        if (converted.length === 1 && converted[0] === child) {
          next.push(child);
        } else {
          next.push(...converted);
        }
      } else {
        next.push(child);
      }
      transformChildren(child);
    }
    parent.children = next;
  }

  return (tree) => {
    transformChildren(tree);
  };
}

// Компонент <a> для react-markdown: якорь #cite-N резолвится в ссылку на документ.
function CiteLink({ href, children, sources, ...props }) {
  const m = /^#cite-(\d+)$/.exec(href || "");
  if (!m) return <a href={href} {...props}>{children}</a>;
  const n = Number(m[1]);
  const s = sources[n - 1];
  const url = inModelContext(s) ? sourceHref(s) : null;
  if (!url) {
    return <span className="cite">{children}</span>;
  }
  return (
    <Link className="cite" href={url} title={s.title}>
      {children}
    </Link>
  );
}

export { CiteLink, documentHref, remarkCiteLinks, sourceHref };
