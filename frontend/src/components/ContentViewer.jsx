"use client";

import { useMemo, useState } from "react";
import MarkdownViewer from "./MarkdownViewer";
import { useI18n } from "@/i18n/LocaleContext";
import { splitLargeTables } from "@/lib/largeTableSplit";

const IMAGE_PATTERN = /!\[[^\]]*\]\([^)]*\)/g;

export default function ContentViewer({ text, docId, stripFrontmatter = false }) {
  const { t, tc } = useI18n();
  const [mode, setMode] = useState("render");
  const [expanded, setExpanded] = useState({});

  const parts = useMemo(() => (mode === "render" ? splitLargeTables(text) : []), [mode, text]);

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
        <div className="content-viewer-body">
          {parts.map((part, i) => {
            if (part.type === "tableRemainder") {
              const open = !!expanded[i];
              return (
                <div key={i} className="table-remainder-block">
                  <div className="table-remainder-note">
                    {tc("content.tableTruncated", part.totalRows, { shown: part.shownRows })}
                  </div>
                  <button
                    className="view-mode-toggle"
                    onClick={() =>
                      setExpanded((prev) => ({ ...prev, [i]: !prev[i] }))
                    }
                  >
                    {open
                      ? t("content.hideRemainingRows")
                      : tc("content.showRemainingRows", part.remainingRows)}
                  </button>
                  {open &&
                    part.chunks.map((chunk, j) => (
                      <MarkdownViewer key={j} text={chunk} docId={docId} />
                    ))}
                </div>
              );
            }
            return (
              <MarkdownViewer
                key={i}
                text={part.text}
                docId={docId}
                stripFrontmatter={stripFrontmatter && i === 0}
                className={part.tablePreview ? "okf-markdown okf-table-preview" : undefined}
              />
            );
          })}
        </div>
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
