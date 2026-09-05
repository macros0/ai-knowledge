"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useI18n } from "@/i18n/LocaleContext";

const BUSY_STATUSES = ["uploaded", "processing", "splitting", "indexing", "paused"];

export default function OkfFileList({
  docId,
  files,
  docStatus,
  totalChunks = 0,
  processedChunks = 0,
  currentChunk = null,
}) {
  const { t } = useI18n();
  const [view, setView] = useState("concepts");
  const [fileList, setFileList] = useState(files);
  const [status, setStatus] = useState(docStatus);
  const [progress, setProgress] = useState({
    total: totalChunks,
    processed: processedChunks,
    current: currentChunk,
  });
  const [chunks, setChunks] = useState(null);
  const [chunkError, setChunkError] = useState(null);
  const timer = useRef(null);
  const viewRef = useRef(view);
  viewRef.current = view;

  useEffect(() => {
    if (view !== "chunks" || chunks !== null) return;
    let cancelled = false;
    fetch(`/api/documents/${docId}/chunks`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (!cancelled) setChunks(data);
      })
      .catch((e) => {
        if (!cancelled) setChunkError(String(e.message || e));
      });
    return () => {
      cancelled = true;
    };
  }, [view, chunks, docId]);

  useEffect(() => {
    let cancelled = false;
    if (!BUSY_STATUSES.includes(status)) return;

    const tick = async () => {
      const [docResp, okfResp] = await Promise.all([
        fetch(`/api/documents/${docId}`),
        fetch(`/api/documents/${docId}/okf`),
      ]);
      if (cancelled) return;

      let nextStatus = status;
      if (docResp.ok) {
        const d = await docResp.json();
        nextStatus = d.status;
        setStatus(d.status);
        setProgress({
          total: d.total_chunks,
          processed: d.processed_chunks,
          current: d.current_chunk,
        });
      }
      if (okfResp.ok) {
        setFileList(await okfResp.json());
      }

      if (viewRef.current === "chunks") {
        try {
          const chunksResp = await fetch(`/api/documents/${docId}/chunks`);
          if (!cancelled && chunksResp.ok) {
            setChunks(await chunksResp.json());
          }
        } catch {
          // best-effort refetch
        }
      }

      if (!cancelled && BUSY_STATUSES.includes(nextStatus)) {
        timer.current = setTimeout(tick, 1500);
      }
    };

    timer.current = setTimeout(tick, 1500);
    return () => {
      cancelled = true;
      clearTimeout(timer.current);
    };
  }, [docId, status]);

  const isBusy = BUSY_STATUSES.includes(status);
  const activeChunk = progress.current ?? progress.processed;
  const VIEWS = [
    { id: "concepts", label: t("okf.view.concepts") },
    { id: "chunks", label: t("okf.view.chunks") },
  ];

  return (
    <>
      {isBusy && progress.total > 0 && (
        <div className="okf-live-progress">
          <span className="live-dot" />
          {t("okf.progress", { active: activeChunk, total: progress.total })}
        </div>
      )}

      <div className="okf-tabs">
        {VIEWS.map((v) => (
          <button
            key={v.id}
            className={`okf-tab${view === v.id ? " active" : ""}`}
            onClick={() => setView(v.id)}
          >
            {v.label}
          </button>
        ))}
      </div>

      {view === "concepts" ? (
        fileList.length === 0 ? (
          <p className="okf-empty">
            {isBusy ? t("okf.conceptsBusy") : t("okf.conceptsNotFound")}
          </p>
        ) : (
          <ul className="okf-list">
            {fileList.map((f) => (
              <li key={f.filename} className="okf-item-row">
                <Link
                  href={`/documents/${docId}/okf/${encodeURIComponent(f.filename)}`}
                  className="okf-list-item"
                >
                  <span className="okf-title">{f.title || f.filename}</span>
                  <span className="okf-meta">
                    <span className={`okf-type okf-type-${f.type}`}>{f.type}</span>
                    {f.tags && f.tags.length > 0 && (
                      <span className="okf-tags">{t("okf.tags", { tags: f.tags.join(", ") })}</span>
                    )}
                    <span className="okf-size">{t("okf.size", { size: (f.size / 1024).toFixed(1) })}</span>
                  </span>
                </Link>
                {f.chunk_index != null && (
                  <Link
                    href={`/documents/${docId}/chunks/${f.chunk_index}`}
                    className="okf-chunk-badge"
                    title={t("okf.chunkTitle", { index: f.chunk_index + 1 })}
                  >
                    {t("okf.chunk", { index: f.chunk_index + 1 })}
                  </Link>
                )}
              </li>
            ))}
            <li>
              <Link
                href={`/documents/${docId}/fulltext`}
                className="okf-list-item okf-fulltext-link"
              >
                <span className="okf-title">{t("okf.fulltext")}</span>
                <span className="okf-meta">
                  <span className="okf-size">{t("okf.fulltextHint")}</span>
                </span>
              </Link>
            </li>
          </ul>
        )
      ) : chunkError ? (
        <p className="okf-empty okf-error">{t("okf.chunksError", { error: chunkError })}</p>
      ) : chunks === null ? (
        <p className="okf-empty">{t("okf.loadingChunks")}</p>
      ) : chunks.length === 0 ? (
        <p className="okf-empty">{t("okf.chunksNotFound")}</p>
      ) : (
        <ul className="okf-list">
          {chunks.map((c) => {
            const chunkFiles = fileList.filter((f) => f.chunk_index === c.index);
            return (
              <li key={c.index}>
                <Link
                  href={`/documents/${docId}/chunks/${c.index}`}
                  className="okf-list-item"
                >
                  <span className="okf-title">{t("okf.chunk", { index: c.index + 1 })}</span>
                  <span className="okf-meta">
                    <span className="okf-size">{t("okf.size", { size: (c.size / 1024).toFixed(1) })}</span>
                    {c.concepts_count > 0 && (
                      <span className="okf-chunk-concepts">{t("okf.conceptsCount", { count: c.concepts_count })}</span>
                    )}
                  </span>
                </Link>
                {chunkFiles.length > 0 && (
                  <div className="okf-chunk-concepts-list">
                    {chunkFiles.map((f) => (
                      <Link
                        key={f.filename}
                        href={`/documents/${docId}/okf/${encodeURIComponent(f.filename)}`}
                        className="okf-chunk-concept"
                      >
                        {f.title || f.filename}
                      </Link>
                    ))}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
