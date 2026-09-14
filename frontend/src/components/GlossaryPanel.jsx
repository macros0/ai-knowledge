"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createGlossaryTerm, friendlyApiError, getChatSettings, getGlossaryTerm, listGlossary } from "@/lib/api";
import { buildGlossaryListParams, keepGlossarySelection } from "@/lib/glossaryUi.mjs";
import { useI18n } from "@/i18n/LocaleContext";
import { useToast } from "./Toast";
import { useAuth } from "@/context/AuthContext";
import GlossaryEditor from "./GlossaryEditor";
import ReferenceLocaleSelect from "./ReferenceLocaleSelect";
import GlossaryAliasCheck, { GlossaryCheckResult, useGlossaryAliasCheck } from "./GlossaryAliasCheck";
import GlossaryRulesEditor from "./GlossaryRulesEditor";
import GlossaryMergeDialog from "./GlossaryMergeDialog";
import { conflictSummary, shouldOfferMerge, glossaryDraftPayload, rejectConflictingCreateAlias, createGlossaryTargetSearch } from "@/lib/glossaryConflicts.mjs";

const PAGE_SIZE = 50;
const KINDS = ["sap_infotype", "sap_transaction", "sap_table", "sap_program", "sap_object", "abbreviation", "business_term"];

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
  const [mergeTarget, setMergeTarget] = useState(null);
  const [mergeQuery, setMergeQuery] = useState("");
  const [mergeResults, setMergeResults] = useState([]);
  const [mergeSearching, setMergeSearching] = useState(false);
  const [pendingEdit, setPendingEdit] = useState(null);
  const [searchMergeTargets] = useState(() => createGlossaryTargetSearch(listGlossary));
  const [createConflict, setCreateConflict] = useState(null);
  const nameCheck = useGlossaryAliasCheck(showCreate ? newTerm.original_name : "", undefined,
    {kind: newTerm.kind, infotypeNumber: newTerm.infotype_number});
  const nameConflicted = Boolean(nameCheck?.conflicts?.length);
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

  useEffect(() => {
    if (!selected || !canManage) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      setMergeSearching(true);
      try {
        const result = await searchMergeTargets(mergeQuery);
        if (!cancelled && result) setMergeResults((result.terms || []).filter((item) => item.id !== selected.id));
      } catch (err) { if (!cancelled) showToast(friendlyApiError(err, t), {type: "error"}); }
      finally { if (!cancelled) setMergeSearching(false); }
    }, 200);
    return () => { cancelled = true; clearTimeout(timer); searchMergeTargets.invalidate(); };
  }, [selected, canManage, mergeQuery, searchMergeTargets, showToast, t]);

  const resetCreate = () => {
    setShowCreate(false);
    setNewTerm({kind: "business_term", original_name: "", original_description: "", canonical_locale: locale, aliases: []});
    setFirstAlias("");
  };

  const finishMerge = () => {
    ++openRequest.current;
    setPendingAlias(false); setPendingEdit(null);
    setMergeTarget(null); setMergeQuery(""); setMergeResults([]);
    setSelected(null); load();
  };

  const create = async (event) => {
    event.preventDefault();
    const returnFocusTo = event.nativeEvent.submitter || document.activeElement;
    if (creating || nameConflicted || !newTerm.original_name.trim()) return;
    setCreating(true);
    const aliasLocale = newTerm.canonical_locale || locale;
    const draft = glossaryDraftPayload(newTerm, firstAlias.trim() ? [{alias: firstAlias, locale: aliasLocale, auto_expand: true, search_enabled: true}] : []);
    try {
      const term = await createGlossaryTerm(draft);
      resetCreate();
      await load();
      if (!pendingAlias) { setMergeTarget(null); setSelected(term); }
      showToast(t(term.has_duplicates ? "admin.glossary.savedWithDuplicates" : "admin.glossary.created"), { type: term.has_duplicates ? "warning" : "success" });
    } catch (err) {
      if (shouldOfferMerge(err.data || err)) {
        const ids = [...new Set(conflictSummary(err.data || err).map((item) => item.termId).filter(Boolean))];
        try {
          const targets = await Promise.all(ids.map(getGlossaryTerm));
          if (targets.length) { setCreateConflict({draft, targets, returnFocusTo, targetId: targets[0].id, conflicts: conflictSummary(err.data || err)}); return; }
        } catch (loadError) { showToast(friendlyApiError(loadError, t), {type: "error"}); }
      }
      showToast(friendlyApiError(err, t), { type: "error" });
    }
    finally { setCreating(false); }
  };

  const mergeCreateName = async (targetId, returnFocusTo) => {
    if (creating || !nameConflicted) return;
    const ids = [...new Set([targetId, ...nameCheck.conflicts.map((item) => item.term_id)].filter(Boolean))];
    const draft = glossaryDraftPayload(newTerm, firstAlias.trim() ? [{alias: firstAlias,
      locale: newTerm.canonical_locale || locale, auto_expand: true, search_enabled: true}] : []);
    setCreating(true);
    try {
      const targets = await Promise.all(ids.map(getGlossaryTerm));
      setCreateConflict({draft, targets, targetId, returnFocusTo, conflicts: nameCheck.conflicts.map((item) => ({key: item.key_value || item.alias || ""}))});
    } catch (err) { showToast(friendlyApiError(err, t), {type: "error"}); }
    finally { setCreating(false); }
  };

  const updateSelected = (term) => {
    setSelected((current) => keepGlossarySelection(current, term));
    setTerms((items) => items.map((item) => item.id === term.id ? term : item));
    load();
  };
  const openTerm = async (id) => {
    if (selected?.id === id) return;
    if (pendingAlias && !window.confirm(t("admin.glossary.unsavedChangesConfirm"))) return;
    const requestId = ++openRequest.current;
    try {
      const term = await getGlossaryTerm(id);
      if (requestId === openRequest.current) {
        setPendingAlias(false); setPendingEdit(null); setMergeTarget(null); setMergeQuery(""); setMergeResults([]);
        setSelected(term);
      }
    } catch (err) { showToast(friendlyApiError(err, t), { type: "error" }); }
  };
  const closeSelected = () => {
    if (pendingAlias && !window.confirm(t("admin.glossary.unsavedChangesConfirm"))) return;
    setPendingAlias(false); setPendingEdit(null); setMergeTarget(null); setMergeQuery(""); setMergeResults([]);
    ++openRequest.current;
    setSelected(null);
  };
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  return (
    <section className="panel admin-panel glossary-panel">
      <div className="glossary-panel-head"><div><h2 className="panel-title">{t("admin.glossary.title")}</h2><p className="muted">{t("admin.glossary.hint")}</p></div>{canManage && <button className="modal-btn" onClick={() => setShowCreate((value) => !value)}>{t("admin.glossary.newTerm")}</button>}</div>
      {searchSettings?.glossary_query_expansion_enabled === false && <div className="glossary-status-warning" role="status">{t("admin.glossary.expansionDisabled")}</div>}
      <GlossaryRulesEditor canManage={canManage} />
      {showCreate && canManage && <form className="glossary-create-form" onSubmit={create}>
        <select value={newTerm.kind} onChange={(e) => setNewTerm((v) => ({ ...v, kind: e.target.value }))}>{KINDS.map((kind) => <option key={kind} value={kind}>{t(`admin.glossary.kind.${kind}`)}</option>)}</select>
        <input value={newTerm.original_name} onChange={(e) => setNewTerm((v) => ({ ...v, original_name: e.target.value }))} placeholder={t("admin.glossary.name")} />
        <GlossaryCheckResult result={nameCheck} nameConflict onOpen={mergeCreateName}
          disabled={creating || (newTerm.kind === "sap_infotype" && !/^[0-9]{4}$/.test(newTerm.infotype_number || ""))} />
        {newTerm.kind === "sap_infotype" && <label className="glossary-field">{t("admin.glossary.mergeField.infotype_number")}
          <input value={newTerm.infotype_number || ""} onChange={(e) => setNewTerm((v) => ({...v, infotype_number: e.target.value}))} inputMode="numeric" pattern="[0-9]{4}" maxLength={4} required />
        </label>}
        <ReferenceLocaleSelect value={newTerm.canonical_locale} onChange={(canonicalLocale) => setNewTerm((v) => ({ ...v, canonical_locale: canonicalLocale }))} labelKey="admin.glossary.locale" />
        <textarea value={newTerm.original_description} onChange={(e) => setNewTerm((v) => ({ ...v, original_description: e.target.value }))} placeholder={t("admin.glossary.description")} rows={2} />
        <label className="glossary-field">{t("admin.glossary.firstAlias")}
          <input value={firstAlias} maxLength={256} onChange={(e) => setFirstAlias(e.target.value)} />
        </label>
        <p className="muted">{t(newTerm.kind === "sap_infotype" ? "admin.glossary.infotypeAliasHint" : "admin.glossary.firstAliasHint")}</p>
        <GlossaryAliasCheck value={firstAlias} onOpen={openTerm} />
        <button className="modal-btn" disabled={creating || nameConflicted || !newTerm.original_name.trim() || (newTerm.kind === "sap_infotype" && !/^[0-9]{4}$/.test(newTerm.infotype_number || ""))}>{t("admin.glossary.create")}</button>
      </form>}
      {createConflict && <GlossaryMergeDialog
        key={createConflict.targetId}
        source={createConflict.draft}
        draft={createConflict.draft}
        returnFocusTo={createConflict.returnFocusTo}
        target={createConflict.targets.find((term) => term.id === createConflict.targetId)}
        targets={createConflict.targets}
        onTargetChange={(id) => setCreateConflict((value) => ({...value, targetId: id}))}
        onClose={() => { setFirstAlias((value) => rejectConflictingCreateAlias(value, createConflict.conflicts)); setCreateConflict(null); }}
        onCompleted={() => { setCreateConflict(null); resetCreate(); load(); }}
      />}
      <div className="glossary-filters">
        <input value={filters.query} onChange={(e) => setFilters((v) => ({ ...v, query: e.target.value, page: 1 }))} placeholder={t("admin.glossary.searchPlaceholder")} />
        <select value={filters.kind} onChange={(e) => setFilters((v) => ({ ...v, kind: e.target.value, page: 1 }))}><option value="">{t("admin.glossary.allKinds")}</option>{KINDS.map((kind) => <option key={kind} value={kind}>{t(`admin.glossary.kind.${kind}`)}</option>)}</select>
        <select value={filters.enabled} onChange={(e) => setFilters((v) => ({ ...v, enabled: e.target.value, page: 1 }))}><option value="">{t("admin.glossary.allStatuses")}</option><option value="true">{t("admin.glossary.enabledOnly")}</option><option value="false">{t("admin.glossary.disabledOnly")}</option></select>
        <label><input type="checkbox" checked={filters.needsReview} onChange={(e) => setFilters((v) => ({ ...v, needsReview: e.target.checked, page: 1 }))} /> {t("admin.glossary.needsReview")}</label>
      </div>
      {loading && <p className="muted">{t("common.loading")}</p>}
      {!loading && !terms.length && <p className="muted">{t("admin.glossary.empty")}</p>}
      <div className="glossary-layout">
        <div className="glossary-table-wrap"><table className="language-table glossary-table"><thead><tr><th>{t("admin.glossary.name")}</th><th>{t("admin.glossary.locale")}</th><th>{t("admin.glossary.aliasCount")}</th><th>{t("admin.glossary.state")}</th></tr></thead><tbody>{terms.map((term) => <tr key={term.id} className={selected?.id === term.id ? "selected" : ""} onClick={() => openTerm(term.id)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && openTerm(term.id)}><td><strong>{term.original_name}</strong></td><td>{term.canonical_locale}</td><td>{term.aliases?.length || 0}</td><td>{term.enabled ? t("admin.glossary.active") : t("admin.glossary.disabled")}{term.has_duplicates && <span className="glossary-duplicate-badge">{t("admin.glossary.duplicate")}</span>}</td></tr>)}</tbody></table><div className="glossary-pagination"><button className="modal-btn" disabled={filters.page <= 1} onClick={() => setFilters((v) => ({ ...v, page: v.page - 1 }))}>←</button><span>{filters.page} / {pageCount}</span><button className="modal-btn" disabled={filters.page >= pageCount} onClick={() => setFilters((v) => ({ ...v, page: v.page + 1 }))}>→</button></div></div>
        {selected && <GlossaryEditor key={selected.id} term={selected} canManage={canManage} onSaved={updateSelected} onOpenTerm={openTerm} onPendingAliasChange={setPendingAlias} onDraftChange={setPendingEdit} onClose={closeSelected} onMergeCompleted={finishMerge} />}
      </div>
      {selected && canManage && <div className="glossary-merge-launcher">
        <label>{t("admin.glossary.mergeWith")}
          <input value={mergeQuery} onChange={(event) => { searchMergeTargets.invalidate(); setMergeQuery(event.target.value); setMergeResults([]); }} placeholder={t("admin.glossary.searchPlaceholder")} />
          <select value={mergeTarget?.id || ""} disabled={mergeSearching} onChange={(event) => setMergeTarget(mergeResults.find((item) => String(item.id) === event.target.value) || null)}>
            <option value="">{t("admin.glossary.mergeChoose")}</option>
            {mergeResults.map((term) => <option key={term.id} value={term.id}>{term.original_name}</option>)}
          </select>
        </label>
        {mergeSearching && <span role="status">{t("common.loading")}</span>}
        {mergeTarget && <GlossaryMergeDialog key={`${selected.id}:${mergeTarget.id}`} source={{...selected, ...pendingEdit}} sourceEdit={pendingEdit} target={mergeTarget} onClose={() => setMergeTarget(null)} onCompleted={finishMerge} />}
      </div>}
    </section>
  );
}
