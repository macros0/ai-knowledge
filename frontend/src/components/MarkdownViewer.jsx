"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useI18n } from "@/i18n/LocaleContext";

function resolveAttachment(src, docId) {
  const clean = String(src || "").replace(/^\.?\//, "");
  if (/^attachments\//.test(clean)) {
    return `/api/documents/${docId}/okf/${clean}`;
  }
  return src;
}

export default function MarkdownViewer({
  text,
  docId,
  stripFrontmatter = false,
  className = "okf-markdown",
  components = {},
  remarkPlugins = [],
  lineMap = null,
  highlightedLines = [],
}) {
  const { t } = useI18n();
  const [lightbox, setLightbox] = useState(null);
  const lastFocusRef = useRef(null);

  const openLightbox = useCallback((src, alt) => {
    lastFocusRef.current = document.activeElement;
    setLightbox({ src, alt });
  }, []);

  const closeLightbox = useCallback(() => {
    setLightbox(null);
  }, []);

  useEffect(() => {
    if (!lightbox) return;
    const onKey = (e) => {
      if (e.key === "Escape") closeLightbox();
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    const focusTarget = document.getElementById("okf-lightbox");
    if (focusTarget) focusTarget.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
      const prev = lastFocusRef.current;
      if (prev && typeof prev.focus === "function") prev.focus();
      lastFocusRef.current = null;
    };
  }, [lightbox, closeLightbox]);

  let body = text || "";
  if (stripFrontmatter) {
    body = body.replace(/^---[\s\S]*?---\s*/, "");
  }

  const highlightedSpanIndices = (node) => {
    if (!node?.position || !lineMap?.length || !highlightedLines.length) return [];
    const start = lineMap[node.position.start.line - 1];
    const end = lineMap[node.position.end.line - 1];
    if (start == null || end == null) return [];
    return highlightedLines.flatMap(([from, to], index) => from <= end && to >= start ? [index] : []);
  };
  const highlightedProps = (node, props) => {
    const indices = highlightedSpanIndices(node);
    const classes = [props.className, indices.length ? "source-highlight" : ""].filter(Boolean).join(" ");
    return { ...props, className: classes || undefined, "data-source-span-indices": indices.length ? indices.join(" ") : undefined };
  };

  const defaultComponents = {
    img({ src, alt, ...props }) {
      const resolved = resolveAttachment(src, docId);
      const label = alt || t("markdown.image");
      return (
        <figure className="okf-figure">
          <img
            className="okf-image"
            src={resolved}
            alt={label}
            loading="lazy"
            onClick={() => openLightbox(resolved, label)}
            {...props}
          />
          <figcaption className="okf-figure-caption">
            <span className="okf-figure-badge" aria-hidden="true">🖼</span>
            {label}
          </figcaption>
        </figure>
      );
    },
    a({ href, children, ...props }) {
      if (href && /^attachments\//.test(href)) {
        return (
          <a href={resolveAttachment(href, docId)} target="_blank" rel="noreferrer" {...props}>
            {children}
          </a>
        );
      }
      return (
        <a href={href} {...props}>
          {children}
        </a>
      );
    },
    table({ node, children, ...props }) {
      return (
        <div className="okf-table-scroll">
          <table {...props}>{children}</table>
        </div>
      );
    },
    p({ node, children, ...props }) {
      // TODO: inline-изображение внутри текста абзаца (`текст ![img](src) ещё`) всё
      // равно даст <figure> внутри <p> — в текущем пайплайне не встречается (снимки
      // страниц идут отдельной строкой), но если формат OKF-генерации изменится,
      // этот случай нужно обработать отдельно.
      const kids = (node?.children || []).filter(
        (c) => c.type !== "text" || (c.value ?? "").trim() !== ""
      );
      if (kids.length === 1 && kids[0].tagName === "img") {
        return <>{children}</>;
      }
      return <p {...highlightedProps(node, props)}>{children}</p>;
    },
    h1({ node, children, ...props }) { return <h1 {...highlightedProps(node, props)}>{children}</h1>; },
    h2({ node, children, ...props }) { return <h2 {...highlightedProps(node, props)}>{children}</h2>; },
    h3({ node, children, ...props }) { return <h3 {...highlightedProps(node, props)}>{children}</h3>; },
    h4({ node, children, ...props }) { return <h4 {...highlightedProps(node, props)}>{children}</h4>; },
    li({ node, children, ...props }) { return <li {...highlightedProps(node, props)}>{children}</li>; },
    blockquote({ node, children, ...props }) { return <blockquote {...highlightedProps(node, props)}>{children}</blockquote>; },
    pre({ node, children, ...props }) { return <pre {...highlightedProps(node, props)}>{children}</pre>; },
    tr({ node, children, ...props }) { return <tr {...highlightedProps(node, props)}>{children}</tr>; },
    ...components,
  };

  return (
    <div className={className}>
      <ReactMarkdown remarkPlugins={[remarkGfm, ...remarkPlugins]} components={defaultComponents}>
        {body}
      </ReactMarkdown>
      {lightbox && (
        <div
          className="okf-lightbox"
          role="dialog"
          aria-modal="true"
          aria-label={lightbox.alt || t("markdown.viewImage")}
          onClick={closeLightbox}
        >
          <button className="okf-lightbox-close" aria-label={t("markdown.close")} onClick={closeLightbox}>
            ✕
          </button>
          <div
            id="okf-lightbox"
            className="okf-lightbox-content"
            tabIndex={-1}
            onClick={(e) => e.stopPropagation()}
          >
            <img src={lightbox.src} alt={lightbox.alt || ""} />
            <div className="okf-lightbox-caption">{lightbox.alt || t("markdown.image")}</div>
          </div>
        </div>
      )}
    </div>
  );
}
