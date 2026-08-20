"use client";

import { useState } from "react";
import MarkdownViewer from "./MarkdownViewer";

export default function ContentViewer({ text, docId, stripFrontmatter = false }) {
  const [mode, setMode] = useState("render");

  if (!text) return null;

  return (
    <div className="content-viewer">
      <button
        className="view-mode-toggle"
        onClick={() => setMode((m) => (m === "render" ? "raw" : "render"))}
      >
        {mode === "render" ? "Показать исходник" : "Показать рендер"}
      </button>
      {mode === "render" ? (
        <MarkdownViewer text={text} docId={docId} stripFrontmatter={stripFrontmatter} />
      ) : (
        <pre className="raw-markdown">{text}</pre>
      )}
    </div>
  );
}