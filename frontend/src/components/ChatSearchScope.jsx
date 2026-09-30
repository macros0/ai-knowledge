"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { friendlyApiError, getChatSearchScope } from "@/lib/api";
import { documentHref } from "@/lib/chatSources";
import { useI18n } from "@/i18n/LocaleContext";

export default function ChatSearchScope({ documents, onRemove, onClear, refreshKey }) {
  const { t } = useI18n();
  const [result, setResult] = useState(null);
  const [revision, setRevision] = useState(0);
  const idsKey = JSON.stringify(documents.map((document) => document.doc_id));

  useEffect(() => {
    const ids = JSON.parse(idsKey);
    if (!ids.length) return;
    let cancelled = false;
    const refresh = () => getChatSearchScope(ids).then((items) => {
      if (!cancelled) setResult({ idsKey, items, error: null });
    }).catch((error) => {
      if (!cancelled) setResult({ idsKey, items: [], error });
    });
    refresh();
    window.addEventListener("focus", refresh);
    return () => { cancelled = true; window.removeEventListener("focus", refresh); };
  }, [idsKey, revision, refreshKey]);

  const current = result?.idsKey === idsKey ? result : null;
  const availability = new Map((current?.items ?? []).map((item) => [item.doc_id, item]));
  const unavailableCount = documents.filter((document) => availability.get(document.doc_id)?.available === false).length;

  return (
    <div className="chat-search-scope active">
      <div className="chat-scope-header">
        <strong>{t("chat.scopeTitle", { count: documents.length })}</strong>
      </div>
      <p className="meta">{t(documents.length ? "chat.scopeDescription" : "chat.scopeEmptyActive")}</p>
      {documents.length > 0 && (
        <details className="chat-scope-details">
          <summary>{t("chat.scopeEditList")}</summary>
          <ul className="chat-scope-documents">
            {documents.map((document) => {
              const item = availability.get(document.doc_id);
              const name = item?.filename || document.filename;
              return (
                <li key={document.doc_id}>
                  {item?.available === false ? <span>{name}</span> : <Link href={documentHref(document)} target="_blank" rel="noopener noreferrer">{name}</Link>}
                  <span className="meta">{t(item?.available === false ? "chat.scopeUnavailable" : !current ? "chat.scopeChecking" : current.error ? "chat.scopeUnknown" : "chat.scopeAvailable")}</span>
                  <button type="button" className="btn ghost" aria-label={t("chat.scopeRemoveDocument", { name })} onClick={() => onRemove(document.doc_id)}>{t("chat.scopeRemove")}</button>
                </li>
              );
            })}
          </ul>
          <div className="chat-scope-actions">
            <button type="button" className="btn ghost" onClick={() => setRevision((value) => value + 1)}>{t("chat.scopeRefresh")}</button>
            <button type="button" className="btn ghost" onClick={onClear}>{t("chat.scopeClear")}</button>
          </div>
        </details>
      )}
      {unavailableCount > 0 && <p className="meta" role="status">{t(unavailableCount === documents.length ? "chat.scopeAllUnavailable" : "chat.scopeSomeUnavailable", { count: unavailableCount })}</p>}
      {current?.error && <p className="meta" role="status">{friendlyApiError(current.error, t)}</p>}
    </div>
  );
}
