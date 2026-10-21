"use client";

import {useEffect, useState} from "react";
import Link from "next/link";
import {useChat} from "@/context/ChatContext";
import {friendlyApiError, getChatSearchScope} from "@/lib/api";
import {addAvailableScopeDocuments, SEARCH_SCOPE_MAX_DOCUMENTS} from "@/lib/chatSearchScope.mjs";
import {useI18n} from "@/i18n/LocaleContext";
import ChatSources from "./ChatSources";

export default function ChatHistorySources({sources, responseMode, open, onToggle, documentSelectionEnabled = true}) {
  const {t} = useI18n();
  const {searchScopeDocuments, setSearchScopeDocuments, setSearchScopeEnabled, sourceView, setSourceView} = useChat();
  const [result, setResult] = useState(null);
  const [selection, setSelection] = useState(null);
  const [notice, setNotice] = useState(null);
  const [revision, setRevision] = useState(0);
  const idsKey = JSON.stringify([...new Set(sources.map(source => source.doc_id).filter(Boolean))]);

  useEffect(() => {
    const ids = JSON.parse(idsKey);
    if (!documentSelectionEnabled || !open || !ids.length) return;
    let cancelled = false;
    getChatSearchScope(ids).then(items => {
      if (!cancelled) setResult({idsKey, items, error:null});
    }).catch(error => {
      if (!cancelled) setResult({idsKey, items:[], error});
    });
    return () => {cancelled = true;};
  }, [documentSelectionEnabled, open, idsKey, revision]);

  const current = open && result?.idsKey === idsKey ? result : null;
  const documents = current?.items ?? [];
  const availableIds = new Set(documents.filter(document => document.available === true).map(document => document.doc_id));
  const selectedIds = selection?.idsKey === idsKey ? selection.ids.filter(id => availableIds.has(id)) : [];
  const toggle = expanded => {
    setResult(null);
    setSelection(null);
    setNotice(null);
    onToggle(expanded);
  };
  const select = (ids, checked) => {
    setSelection(previous => {
      const next = new Set(previous?.idsKey === idsKey ? previous.ids : []);
      for (const id of ids) {
        if (checked && availableIds.has(id)) next.add(id);
        else next.delete(id);
      }
      return {idsKey, ids:[...next].filter(id => availableIds.has(id))};
    });
    setNotice(null);
  };
  const add = () => {
    if (!selectedIds.length) return;
    try {
      const next = addAvailableScopeDocuments(searchScopeDocuments, documents, selectedIds);
      setSearchScopeDocuments(next);
      setSearchScopeEnabled(true);
      setNotice({key:"chat.scopeAdded", count:next.length - searchScopeDocuments.length});
      setSelection(null);
    } catch (error) {
      if (!(error instanceof RangeError)) throw error;
      setNotice({key:"chat.scopeLimit", max:SEARCH_SCOPE_MAX_DOCUMENTS});
    }
  };

  return <ChatSources sources={sources} responseMode={responseMode} open={open} onToggle={toggle}
    view={sourceView} onViewChange={setSourceView}
    documentSelection={documentSelectionEnabled ? {documents, selectedIds, onSelect:select} : undefined}>
    {documentSelectionEnabled && <>
      {!current && idsKey !== "[]" && <p className="meta" role="status">{t("chat.scopeChecking")}</p>}
      {current?.error && <div role="alert">
        <p className="meta">{friendlyApiError(current.error, t)}</p>
        <button type="button" className="btn ghost" onClick={() => {setResult(null); setRevision(value => value + 1);}}>{t("chat.scopeRefresh")}</button>
      </div>}
      {selectedIds.length > 0 && <div className="source-selection-actions">
        <button type="button" className="btn" onClick={add}>{t("chat.scopeAddSelected")}</button>
        <span className="meta">{t("chat.scopeCurrentVersions")}</span>
      </div>}
      {notice && <div className="source-selection-actions">
        <span className="meta" role="status">{t(notice.key, notice)}</span>
        {notice.key === "chat.scopeAdded" && <Link href="/chat" className="btn ghost">{t("chat.scopeOpenChat")}</Link>}
      </div>}
    </>}
  </ChatSources>;
}
