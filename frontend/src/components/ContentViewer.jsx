"use client";

import { useState } from "react";
import MarkdownViewer from "./MarkdownViewer";
import { useI18n } from "@/i18n/LocaleContext";

const IMAGE_PATTERN = /!\[[^\]]*\]\([^)]*\)/g;

export default function ContentViewer({ text, docId, stripFrontmatter = false }) {
  const { t, tc } = useI18n();
  const [mode, setMode] = useState("render");

  if (!text) return null;

  const { imageCount, hasText } = countImagesAndText(text);
  const imageOnly = imageCount > 0 && !hasText;

  return (
    <div className="content-viewer">
      <button
        className="view-mode-toggle"
        onClick={() => setMode((m) => (m === "render" ? "raw" : "render"))}
      >
        {mode === "render" ? t("content.showRaw") : t("content.showRender")}
      </button>
      {imageOnly && (
        <div className="okf-image-banner" role="note">
          {tc("content.imageOnly", imageCount)}
        </div>
      )}
      {mode === "render" ? (
        <MarkdownViewer text={text} docId={docId} stripFrontmatter={stripFrontmatter} />
      ) : (
        <pre className="raw-markdown">{text}</pre>
      )}
    </div>
  );
}

function countImagesAndText(text) {
  const images = text.match(IMAGE_PATTERN) || [];
  const withoutImages = text.replace(IMAGE_PATTERN, "").trim();
  return { imageCount: images.length, hasText: withoutImages.length > 0 };
}
