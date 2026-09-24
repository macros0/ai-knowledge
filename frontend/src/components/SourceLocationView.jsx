"use client";

import { useEffect, useRef, useState } from "react";
import ContentViewer from "@/components/ContentViewer";
import { useI18n } from "@/i18n/LocaleContext";

export default function SourceLocationView({ docId, chunks, location, heading, showAll = false }) {
  const { t } = useI18n();
  const current = useRef(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const spans = location?.spans || [];
  const selectedIndex = Math.min(activeIndex, Math.max(0, spans.length - 1));
  const targetChunk = chunks.find(({ chunk_index }) => chunk_index === location?.chunk_index);

  useEffect(() => {
    const root = current.current;
    if (!root) return;
    let scrolledNode = null;
    const scrollToEvidence = () => {
      const target = spans.length
        ? root.querySelector(`[data-source-span-indices~="${selectedIndex}"]`)
        : root;
      if (target && target !== scrolledNode) {
        target.scrollIntoView({ block: "center", behavior: "auto" });
        scrolledNode = target;
      }
    };
    const observer = new MutationObserver(scrollToEvidence);
    observer.observe(root, { childList: true, subtree: true });
    const frame = window.requestAnimationFrame(scrollToEvidence);
    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [location?.chunk_index, location?.status, selectedIndex, spans.length]);

  if (!targetChunk) return <p className="source-location-note">{t("sourceLocation.unavailable")}</p>;

  return (
    <section className="source-location-view" aria-label={t("sourceLocation.title")}>
      <header className="source-location-header">
        <strong>{heading || t("sourceLocation.title")}</strong>
        {location.status === "exact" || location.status === "recovered" ? (
          <span>{t(location.status === "recovered" ? "sourceLocation.recovered" : "sourceLocation.exact", { index: location.chunk_index + 1 })}</span>
        ) : (
          <span>{t("sourceLocation.chunkOnly", { index: location.chunk_index + 1 })}</span>
        )}
        {spans.length > 1 && (
          <nav className="source-location-navigation" aria-label={t("sourceLocation.navigation")}>
            <button type="button" aria-label={t("sourceLocation.previous")} disabled={selectedIndex === 0} onClick={() => setActiveIndex(selectedIndex - 1)}>←</button>
            <span aria-live="polite">{t("sourceLocation.position", { index: selectedIndex + 1, total: spans.length })}</span>
            <button type="button" aria-label={t("sourceLocation.next")} disabled={selectedIndex === spans.length - 1} onClick={() => setActiveIndex(selectedIndex + 1)}>→</button>
          </nav>
        )}
      </header>
      {spans[selectedIndex]?.quote && (
        <blockquote className="source-location-quotes">
          <p>{spans[selectedIndex].quote}</p>
        </blockquote>
      )}
      {chunks.filter(({ chunk_index }) => showAll || chunk_index === location.chunk_index).map((chunk) => (
        <div
          key={chunk.chunk_index}
          ref={chunk.chunk_index === targetChunk.chunk_index ? current : undefined}
          id={`source-chunk-${chunk.chunk_index}`}
          className={`source-location-chunk${chunk.chunk_index === targetChunk.chunk_index ? " is-source-target" : ""}`}
        >
          {showAll && <h2 className="source-chunk-heading">{t("okf.page.chunkH1", { index: chunk.chunk_index + 1 })}</h2>}
          <ContentViewer text={chunk.content} docId={docId} sourceSpans={chunk.chunk_index === targetChunk.chunk_index ? spans : []} />
        </div>
      ))}
    </section>
  );
}
