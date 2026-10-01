"use client";

import { useEffect, useState } from "react";
import { friendlyApiError, getDocumentSources } from "@/lib/api";
import { canDownloadSource, sourceAncestors, sourceDepth, sourceDownloadUrl, sourceHasChildren, sourceStatusKey, sourceLocationKey, sourceWarningKeys, visibleSourceIds } from "@/lib/sourceTree.mjs";
import { useI18n } from "@/i18n/LocaleContext";
import MailMetadata from "./MailMetadata";

function sourceMeta(source, t) {
  const meta = source.metadata || {};
  const values = [];
  if (meta.sender && source.kind !== "mail" && meta.mail !== true) values.push(meta.sender);
  const locationKey = sourceLocationKey(source);
  if (locationKey) values.push(t(locationKey));
  return values;
}

export default function DocumentSources({ docId, focusedSourceId = null }) {
  const { t } = useI18n();
  const [sources, setSources] = useState(null);
  const [error, setError] = useState(null);
  const [expandedSourceIds, setExpandedSourceIds] = useState(new Set());

  useEffect(() => {
    let cancelled = false;
    getDocumentSources(docId)
      .then((data) => {
        if (!cancelled) {
          const loadedSources = data.sources || [];
          setSources(loadedSources);
          setExpandedSourceIds(new Set(sourceAncestors(loadedSources, focusedSourceId)));
        }
      })
      .catch((loadError) => {
        if (!cancelled) setError(loadError);
      });
    return () => { cancelled = true; };
  }, [docId, focusedSourceId]);

  const toggleSource = (sourceId) => {
    setExpandedSourceIds((current) => {
      const next = new Set(current);
      if (next.has(sourceId)) next.delete(sourceId);
      else next.add(sourceId);
      return next;
    });
  };

  const visibleIds = new Set(visibleSourceIds(sources, expandedSourceIds));

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
          {sources.filter((source) => visibleIds.has(source.source_id)).map((source) => {
            const statusKey = sourceStatusKey(source.extraction_status);
            const meta = sourceMeta(source, t);
            const mailSubject = source.metadata?.subject;
            const download = canDownloadSource(source);
            const sourceName = mailSubject || source.display_name;
            const collapsible = source.source_id !== "root" && sourceHasChildren(sources, source.source_id);
            const expanded = source.source_id === "root" || expandedSourceIds.has(source.source_id);
            return (
              <li
                key={source.source_id}
                className="document-sources-item"
                style={{ "--source-depth": sourceDepth(source.source_id) }}
              >
                <div className="document-sources-entry">
                  {collapsible ? (
                    <button
                      type="button"
                      className="document-sources-toggle"
                      onClick={() => toggleSource(source.source_id)}
                      aria-expanded={expanded}
                      aria-label={t(expanded ? "sources.collapse" : "sources.expand", { name: sourceName })}
                      title={t(expanded ? "sources.collapse" : "sources.expand", { name: sourceName })}
                    >
                      <span aria-hidden="true">{expanded ? "▾" : "▸"}</span>
                    </button>
                  ) : <span className="document-sources-toggle-spacer" aria-hidden="true" />}
                  <div className="document-sources-row">
                    <div>
                      <strong>{sourceName}</strong>
                      {mailSubject && source.display_name !== mailSubject && (
                        <span className="document-sources-filename">{source.display_name}</span>
                      )}
                      {meta.length > 0 && <span className="document-sources-meta">{meta.join(" · ")}</span>}
                      <MailMetadata source={source} />
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
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
