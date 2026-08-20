"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

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
}) {
  let body = text || "";
  if (stripFrontmatter) {
    body = body.replace(/^---[\s\S]*?---\s*/, "");
  }

  const defaultComponents = {
    img({ src, alt, ...props }) {
      return <img className="okf-image" src={resolveAttachment(src, docId)} alt={alt || ""} {...props} />;
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
    ...components,
  };

  return (
    <div className={className}>
      <ReactMarkdown remarkPlugins={[remarkGfm, ...remarkPlugins]} components={defaultComponents}>
        {body}
      </ReactMarkdown>
    </div>
  );
}
