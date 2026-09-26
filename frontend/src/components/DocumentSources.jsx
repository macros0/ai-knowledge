"use client";

import { useEffect, useState } from "react";
import { friendlyApiError, getDocumentSources } from "@/lib/api";
import { canDownloadSource, sourceDepth, sourceDownloadUrl, sourceStatusKey, sourceLocationKey, sourceWarningKeys, sourceDateText } from "@/lib/sourceTree.mjs";
import { useI18n } from "@/i18n/LocaleContext";

function sourceMeta(source, fmtDateTime, t) {
  const meta = source.metadata || {};
  const values = [];
  if (meta.sender) values.push(meta.sender);
  const date = sourceDateText(source, fmtDateTime, t);
  if (date) values.push(date);
  const locationKey = sourceLocationKey(source);
  if (locationKey) values.push(t(locationKey));
  return values;
}

export default function DocumentSources({ docId }) {
  const { t, fmtDateTime } = useI18n();
  const [sources, setSources] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    getDocumentSources(docId)
      .then((data) => {
        if (!cancelled) setSources(data.sources || []);
      })
      .catch((loadError) => {
        if (!cancelled) setError(loadError);
      });
    return () => { cancelled = true; };
  }, [docId]);

  return (
    <section className="document-sources" aria-labelledby="document-sources-title">
      <h2 id="document-sources-title">{t("sources.title")}</h2>
      {error ? (
        <p className="document-sources-error" role="alert">
          {t("sources.loadError", { error: friendlyApiError(error, t) })}
        </p>
      ) : sources === null ? (
        <p className="document-sources-empty">{t("sources.loading")}</p>
      ) : sources.length === 0 ? (
        <p className="document-sources-empty">{t("sources.empty")}</p>
      ) : (
        <ol className="document-sources-list">
          {sources.map((source) => {
            const statusKey = sourceStatusKey(source.extraction_status);
            const meta = sourceMeta(source, fmtDateTime, t);
            const mailSubject = source.metadata?.subject;
            const download = canDownloadSource(source);
            return (
              <li
                key={source.source_id}
                className="document-sources-item"
                style={{ "--source-depth": sourceDepth(source.source_id) }}
              >
                <div className="document-sources-row">
                  <div>
                    <strong>{mailSubject || source.display_name}</strong>
                    {mailSubject && source.display_name !== mailSubject && (
                      <span className="document-sources-filename">{source.display_name}</span>
                    )}
                    {meta.length > 0 && <span className="document-sources-meta">{meta.join(" · ")}</span>}
                    {sourceWarningKeys(source).map((key) => (
                      <span className="document-sources-meta" key={key}>{t(key)}</span>
                    ))}
                  </div>
                  <div className="document-sources-actions">
                    {statusKey && <span className="document-sources-status">{t(statusKey)}</span>}
                    {download && (
                      <a className="document-sources-download" href={sourceDownloadUrl(docId, source.source_id)} download
                        aria-label={t(source.artifact_kind === "container_only" ? "sources.downloadContainerNamed" : "sources.downloadNamed", {
                          name: source.display_name || mailSubject || source.source_id,
                        })}>
                        {t(source.artifact_kind === "container_only" ? "sources.downloadContainer" : "sources.download")}
                      </a>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
