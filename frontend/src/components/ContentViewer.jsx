"use client";

import { useState } from "react";
import MarkdownViewer from "./MarkdownViewer";

const IMAGE_PATTERN = /!\[[^\]]*\]\([^)]*\)/g;

function pluralImages(n) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return "1 сканированное изображение страницы";
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14))
    return `${n} сканированных изображения страниц`;
  return `${n} сканированных изображений страниц`;
}

function countImagesAndText(text) {
  const images = text.match(IMAGE_PATTERN) || [];
  const withoutImages = text.replace(IMAGE_PATTERN, "").trim();
  return { imageCount: images.length, hasText: withoutImages.length > 0 };
}

export default function ContentViewer({ text, docId, stripFrontmatter = false }) {
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
        {mode === "render" ? "Показать исходник" : "Показать рендер"}
      </button>
      {imageOnly && (
        <div className="okf-image-banner" role="note">
          Содержимое представлено {pluralImages(imageCount)}. Текстовый слой отсутствует —
          контент доступен как изображения.
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
