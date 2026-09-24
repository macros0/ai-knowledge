"use client";

import { useEffect, useMemo, useState } from "react";
import MarkdownViewer from "./MarkdownViewer";
import { useI18n } from "@/i18n/LocaleContext";
import { splitLargeTables } from "@/lib/largeTableSplit";
import { sourceSpansToLineRanges, splitRawSourceSpans } from "@/lib/sourceHighlight.mjs";

const IMAGE_PATTERN = /!\[[^\]]*\]\([^)]*\)/g;

export default function ContentViewer({ text, docId, stripFrontmatter = false, sourceSpans = [] }) {
  const { t, tc } = useI18n();
  const [mode, setMode] = useState("render");
  const [expanded, setExpanded] = useState({});

  const parts = useMemo(() => (mode === "render" ? splitLargeTables(text) : []), [mode, text]);
  const highlightedLines = useMemo(
    () => sourceSpansToLineRanges(text, sourceSpans),
    [sourceSpans, text],
  );

  useEffect(() => {
    if (!highlightedLines.length || !parts.length) return;
    setExpanded((previous) => {
      const next = { ...previous };
      parts.forEach((part, index) => {
        if (part.type !== "tableRemainder") return;
        const containsEvidence = (part.chunkLineMaps || []).some((lineMap) =>
          highlightedLines.some(([from, to]) => lineMap.some((line) => line >= from && line <= to))
        );
        if (containsEvidence) next[index] = true;
      });
      return next;
    });
  }, [highlightedLines, parts]);

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
                      <MarkdownViewer key={j} text={chunk} docId={docId} lineMap={part.chunkLineMaps?.[j]} highlightedLines={highlightedLines} />
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
                lineMap={part.lineMap}
                highlightedLines={highlightedLines}
                className={part.tablePreview ? "okf-markdown okf-table-preview" : undefined}
              />
            );
          })}
        </div>
      ) : (
        <pre className="raw-markdown">{renderRawHighlights(text, sourceSpans)}</pre>
      )}
    </div>
  );
}

function renderRawHighlights(text, spans) {
  if (!spans?.length) return text;
  return splitRawSourceSpans(text, spans).map(({ text: segment, spanIndex }, index) =>
    spanIndex == null ? segment : (
      <mark key={index} className="source-highlight-mark" data-source-span-indices={spanIndex}>
        {segment}
      </mark>
    )
  );
}

function countImagesAndText(text) {
  const images = text.match(IMAGE_PATTERN) || [];
  const withoutImages = text.replace(IMAGE_PATTERN, "").trim();
  return { imageCount: images.length, hasText: withoutImages.length > 0 };
}
