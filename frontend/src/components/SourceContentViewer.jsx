"use client";

import { useEffect, useRef, useState } from "react";
import ContentViewer from "@/components/ContentViewer";
import { sourceAncestors, visibleSourceContentIds, visibleSourceIds } from "@/lib/sourceTree.mjs";
import { useI18n } from "@/i18n/LocaleContext";
import MailMetadata from "./MailMetadata";

function normalizedSourceId(chunk) {
  return chunk.source_id || "root";
}

function ChunkContent({chunk, docId, preserveLineBreaks, initiallyVisible}) {
  const {t} = useI18n();
  const container = useRef(null);
  const [ready, setReady] = useState(initiallyVisible);
  useEffect(() => {
    if (ready || !container.current) return;
    if (typeof IntersectionObserver === "undefined") {
      const frame = window.requestAnimationFrame(() => setReady(true));
      return () => window.cancelAnimationFrame(frame);
    }
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) {
        observer.disconnect();
        setReady(true);
      }
    }, {rootMargin: "600px"});
    observer.observe(container.current);
    return () => observer.disconnect();
  }, [ready]);
  return (
    <div ref={container} className="source-content-chunk" data-chunk-index={chunk.chunk_index}
      aria-busy={!ready} style={ready ? undefined : {minHeight: 160}}>
      {ready ? <ContentViewer text={chunk.content} docId={docId} preserveLineBreaks={preserveLineBreaks} />
        : <p className="source-chunk-heading">{t("okf.page.chunkH1", {index: chunk.chunk_index + 1})}</p>}
    </div>
  );
}

export default function SourceContentViewer({ docId, chunks, sources, focusedSourceId = null }) {
  const { t } = useI18n();
  const [expandedSourceIds, setExpandedSourceIds] = useState(() => new Set([
    ...sourceAncestors(sources, focusedSourceId),
    ...(focusedSourceId && focusedSourceId !== "root" ? [focusedSourceId] : []),
  ]));
  const sourcesById = new Map((sources || []).map((source) => [source.source_id, source]));
  const visibleHeaders = new Set(visibleSourceIds(sources, expandedSourceIds));
  const visibleContent = new Set(visibleSourceContentIds(sources, expandedSourceIds));

  const toggleSource = (sourceId) => {
    setExpandedSourceIds((current) => {
      const next = new Set(current);
      if (next.has(sourceId)) next.delete(sourceId);
      else next.add(sourceId);
      return next;
    });
  };

  return (
    <div className="source-content-viewer">
      {(chunks || []).map((chunk, index) => {
        const sourceId = normalizedSourceId(chunk);
        const source = sourcesById.get(sourceId);
        const previousSourceId = index > 0 ? normalizedSourceId(chunks[index - 1]) : null;
        const startsSourceRun = sourceId !== "root" && sourceId !== previousSourceId;
        const startsMailRun = sourceId !== previousSourceId;
        const sourceVisible = !source || visibleHeaders.has(sourceId);
        const contentVisible = sourceId === "root" || !source || visibleContent.has(sourceId);
        const sourceName = source?.metadata?.subject || source?.display_name || sourceId;
        const expanded = expandedSourceIds.has(sourceId);

        if (!sourceVisible) return null;
        return (
          <section className="source-content-section" key={chunk.chunk_index}>
            {startsSourceRun && (
              <button
                type="button"
                className="source-content-toggle"
                onClick={() => toggleSource(sourceId)}
                aria-expanded={expanded}
                aria-label={t(expanded ? "sources.collapse" : "sources.expand", { name: sourceName })}
              >
                <span aria-hidden="true">{expanded ? "▾" : "▸"}</span>
                <strong>{sourceName}</strong>
              </button>
            )}
            {startsMailRun && <MailMetadata source={source} />}
            {contentVisible && <ChunkContent chunk={chunk} docId={docId} initiallyVisible={index === 0}
              preserveLineBreaks={source?.kind === "mail" || source?.metadata?.mail === true} />}
          </section>
        );
      })}
    </div>
  );
}
