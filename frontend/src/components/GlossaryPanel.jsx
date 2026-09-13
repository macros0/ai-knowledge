"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createGlossaryTerm, friendlyApiError, getChatSettings, getGlossaryTerm, listGlossary } from "@/lib/api";
import { buildGlossaryListParams, keepGlossarySelection } from "@/lib/glossaryUi.mjs";
import { useI18n } from "@/i18n/LocaleContext";
import { useToast } from "./Toast";
import { useAuth } from "@/context/AuthContext";
import GlossaryEditor from "./GlossaryEditor";
import ReferenceLocaleSelect from "./ReferenceLocaleSelect";
import GlossaryAliasCheck from "./GlossaryAliasCheck";

const PAGE_SIZE = 50;
const KINDS = ["sap_infotype", "sap_transaction", "sap_table", "abbreviation", "business_term"];

export default function GlossaryPanel() {
  const { t, locale } = useI18n();
  const { mode, hasRole } = useAuth();
  const canManage = mode === "disabled" || hasRole("admin");
  const { showToast } = useToast();
  const [terms, setTerms] = useState([]);
  const [selected, setSelected] = useState(null);
  const [filters, setFilters] = useState({ query: "", kind: "", enabled: "", needsReview: false, page: 1 });
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [newTerm, setNewTerm] = useState({ kind: "business_term", original_name: "", original_description: "", canonical_locale: locale, aliases: [] });
  const [firstAlias, setFirstAlias] = useState("");
  const [creating, setCreating] = useState(false);
  const [pendingAlias, setPendingAlias] = useState(false);
  const [searchSettings, setSearchSettings] = useState(null);
  const selectedRequest = useRef(0);
  const openRequest = useRef(0);

  const load = useCallback(async () => {
    const requestId = ++selectedRequest.current;
    setLoading(true);
    try {
      const result = await listGlossary(buildGlossaryListParams({ ...filters, pageSize: PAGE_SIZE }));
      if (requestId !== selectedRequest.current) return;
      setTerms(result.terms || []); setTotal(result.total || 0);

    } catch (err) { showToast(friendlyApiError(err, t), { type: "error" }); }
    finally { if (requestId === selectedRequest.current) setLoading(false); }
  }, [filters, showToast, t]);

  // The list is an external resource keyed by the current filters.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    let cancelled = false;
    getChatSettings()
      .then((settings) => { if (!cancelled) setSearchSettings(settings); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const create = async (event) => {
    event.preventDefault();
    if (creating || !newTerm.original_name.trim()) return;
    setCreating(true);
    try {
      const aliasLocale = newTerm.canonical_locale || locale;
      const term = await createGlossaryTerm({ ...newTerm, aliases: firstAlias.trim() ? [{ alias: firstAlias.trim(), locale: aliasLocale === "und" ? null : aliasLocale, auto_expand: true, search_enabled: true }] : [], original_description: newTerm.original_description || null, canonical_locale: newTerm.canonical_locale || "und" });
      setShowCreate(false); setNewTerm({ kind: "business_term", original_name: "", original_description: "", canonical_locale: locale, aliases: [] });
      setFirstAlias("");
      await load(); setSelected(term);
      showToast(t(term.has_duplicates ? "admin.glossary.savedWithDuplicates" : "admin.glossary.created"), { type: term.has_duplicates ? "warning" : "success" });
    } catch (err) { showToast(friendlyApiError(err, t), { type: "error" }); }
    finally { setCreating(false); }
  };

  const updateSelected = (term) => {
    setSelected((current) => keepGlossarySelection(current, term));
    setTerms((items) => items.map((item) => item.id === term.id ? term : item));
    load();
  };
  const openTerm = async (id) => {
    if (selected?.id !== id && pendingAlias && !window.confirm(t("admin.glossary.unsavedAliasConfirm"))) return;
    setPendingAlias(false);
    const requestId = ++openRequest.current;
    try {
      const term = await getGlossaryTerm(id);
      if (requestId === openRequest.current) setSelected(term);
    } catch (err) { showToast(friendlyApiError(err, t), { type: "error" }); }
  };
  const closeSelected = () => {
    if (pendingAlias && !window.confirm(t("admin.glossary.unsavedAliasConfirm"))) return;
    setPendingAlias(false);
    ++openRequest.current;
    setSelected(null);
  };
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  return (
    <section className="panel admin-panel glossary-panel">
      <div className="glossary-panel-head"><div><h2 className="panel-title">{t("admin.glossary.title")}</h2><p className="muted">{t("admin.glossary.hint")}</p></div>{canManage && <button className="modal-btn" onClick={() => setShowCreate((value) => !value)}>{t("admin.glossary.newTerm")}</button>}</div>
      {searchSettings?.glossary_query_expansion_enabled === false && <div className="glossary-status-warning" role="status">{t("admin.glossary.expansionDisabled")}</div>}
      {showCreate && canManage && <form className="glossary-create-form" onSubmit={create}>
        <select value={newTerm.kind} onChange={(e) => setNewTerm((v) => ({ ...v, kind: e.target.value }))}>{KINDS.map((kind) => <option key={kind}>{kind}</option>)}</select>
        <input value={newTerm.original_name} onChange={(e) => setNewTerm((v) => ({ ...v, original_name: e.target.value }))} placeholder={t("admin.glossary.name")} />
        <ReferenceLocaleSelect value={newTerm.canonical_locale} onChange={(canonicalLocale) => setNewTerm((v) => ({ ...v, canonical_locale: canonicalLocale }))} labelKey="admin.glossary.locale" />
        <textarea value={newTerm.original_description} onChange={(e) => setNewTerm((v) => ({ ...v, original_description: e.target.value }))} placeholder={t("admin.glossary.description")} rows={2} />
        <label className="glossary-field">{t("admin.glossary.firstAlias")}
          <input value={firstAlias} maxLength={256} onChange={(e) => setFirstAlias(e.target.value)} />
        </label>
        <p className="muted">{t("admin.glossary.firstAliasHint")}</p>
        <GlossaryAliasCheck value={firstAlias} onOpen={openTerm} />
        <button className="modal-btn" disabled={creating || !newTerm.original_name.trim()}>{t("admin.glossary.create")}</button>
      </form>}
      <div className="glossary-filters">
        <input value={filters.query} onChange={(e) => setFilters((v) => ({ ...v, query: e.target.value, page: 1 }))} placeholder={t("admin.glossary.searchPlaceholder")} />
        <select value={filters.kind} onChange={(e) => setFilters((v) => ({ ...v, kind: e.target.value, page: 1 }))}><option value="">{t("admin.glossary.allKinds")}</option>{KINDS.map((kind) => <option key={kind}>{kind}</option>)}</select>
        <select value={filters.enabled} onChange={(e) => setFilters((v) => ({ ...v, enabled: e.target.value, page: 1 }))}><option value="">{t("admin.glossary.allStatuses")}</option><option value="true">{t("admin.glossary.enabledOnly")}</option><option value="false">{t("admin.glossary.disabledOnly")}</option></select>
        <label><input type="checkbox" checked={filters.needsReview} onChange={(e) => setFilters((v) => ({ ...v, needsReview: e.target.checked, page: 1 }))} /> {t("admin.glossary.needsReview")}</label>
      </div>
      {loading && <p className="muted">{t("common.loading")}</p>}
      {!loading && !terms.length && <p className="muted">{t("admin.glossary.empty")}</p>}
      <div className="glossary-layout">
        <div className="glossary-table-wrap"><table className="language-table glossary-table"><thead><tr><th>{t("admin.glossary.name")}</th><th>{t("admin.glossary.locale")}</th><th>{t("admin.glossary.aliasCount")}</th><th>{t("admin.glossary.state")}</th></tr></thead><tbody>{terms.map((term) => <tr key={term.id} className={selected?.id === term.id ? "selected" : ""} onClick={() => openTerm(term.id)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && openTerm(term.id)}><td><strong>{term.original_name}</strong></td><td>{term.canonical_locale}</td><td>{term.aliases?.length || 0}</td><td>{term.enabled ? t("admin.glossary.active") : t("admin.glossary.disabled")}{term.has_duplicates && <span className="glossary-duplicate-badge">{t("admin.glossary.duplicate")}</span>}</td></tr>)}</tbody></table><div className="glossary-pagination"><button className="modal-btn" disabled={filters.page <= 1} onClick={() => setFilters((v) => ({ ...v, page: v.page - 1 }))}>←</button><span>{filters.page} / {pageCount}</span><button className="modal-btn" disabled={filters.page >= pageCount} onClick={() => setFilters((v) => ({ ...v, page: v.page + 1 }))}>→</button></div></div>
        {selected && <GlossaryEditor key={selected.id} term={selected} canManage={canManage} onSaved={updateSelected} onOpenTerm={openTerm} onPendingAliasChange={setPendingAlias} onClose={closeSelected} />}
      </div>
    </section>
  );
}
