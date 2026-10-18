"use client";

import { apiToast } from "@/lib/apiToast.mjs";

import { useEffect, useState } from "react";
import { withReferenceEditLock, createGlossaryRule, deleteGlossaryRule, friendlyApiError, listGlossaryRules,
  updateGlossaryRule, previewGlossaryRuleMerge, commitGlossaryRuleMerge, previewGlossaryRule } from "@/lib/api";
import { useReferenceEditSession } from "@/hooks/useReferenceEditSession";
import { useReferenceMergeSession } from "@/hooks/useReferenceMergeSession";
import ReferenceConflictDialog from "./ReferenceConflictDialog";
import ReferenceEditNotice from "./ReferenceEditNotice";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";
import { useGlossaryDialogFocus } from "@/lib/useGlossaryDialogFocus";
import { formatGlossaryRulePrefixes, parseGlossaryRulePrefixes } from "@/lib/glossaryRules.mjs";

const empty = { name: "", number_from: "0000", number_to: "9999", prefixes: '"IT"', enabled: true };
const rangeText = (rule) => [rule.number_from, rule.number_to].map((value) => String(value).padStart(4, "0")).join("–");
const RULE_FIELDS = ["name", "number_from", "number_to", "prefixes", "enabled"];
const editableRule = rule => ({...rule, number_from: String(rule.number_from).padStart(4, "0"), number_to: String(rule.number_to).padStart(4, "0"), prefixes: formatGlossaryRulePrefixes(rule.prefixes)});
const payloadFor = (draft) => ({name: draft.name, number_from: draft.number_from, number_to: draft.number_to,
  prefixes: parseGlossaryRulePrefixes(draft.prefixes), enabled: draft.enabled});

export default function GlossaryRulesEditor({ canManage = false }) {
  const { t } = useI18n();
  const { showToast } = useToast();
  const [rules, setRules] = useState([]);
  const [draft, setDraft] = useState(empty);
  const [editingId, setEditingId] = useState(null);
  const [editBase, setEditBase] = useState(null);
  const [conflict, setConflict] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [merge, setMerge] = useState(null);
  const [proposal, setProposal] = useState(null);
  const editSession = useReferenceEditSession({resource: {resource_type: "glossary_rule", resource_id: String(editingId || 0)}, enabled: Boolean(editingId) && canManage,
    onAcquired: async (_status, isCurrent) => {
      const fresh = (await listGlossaryRules()).find(item => item.id === editingId);
      if (!fresh) throw Object.assign(new Error("Rule removed"), {code: "not_found"});
      if (isCurrent() && fresh.reference_revision !== editBase?.reference_revision) {
        setConflict({base: editBase, local: {...draft}, remote: editableRule(fresh)});
      }
    }});
  const mergeSession = useReferenceMergeSession({resourceType: "glossary_rule", sourceId: merge?.source?.id,
    targetId: merge?.targetId || 0, sourceSession: editingId && merge?.source?.id === editingId ? editSession : null, enabled: Boolean(merge)});
  const writable = !editingId || editSession.state === "editing";
  const [query, setQuery] = useState("");
  const [rulePreview, setRulePreview] = useState(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const previewKey = JSON.stringify([draft, query]);
  const dialogRef = useGlossaryDialogFocus(Boolean(merge), () => { setMerge(null); setProposal(null); }, busy, merge?.returnFocusTo);

  const load = async () => {
    setLoading(true);
    try { setRules(await listGlossaryRules()); }
    catch (err) { showToast(apiToast(err, t, { type: "error" })); }
    finally { setLoading(false); }
  };
  useEffect(() => {
    let active = true;
    listGlossaryRules().then((items) => { if (active) setRules(items); })
      .catch((err) => { if (active) showToast(apiToast(err, t, {type: "error"})); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [showToast, t]);

  const save = async (event) => {
    event.preventDefault();
    const returnFocusTo = event.nativeEvent.submitter || document.activeElement;
    if (!canManage || !draft.name.trim() || busy || !writable || conflict || merge) return;
    setBusy(true);
    let payload;
    try {
      payload = payloadFor(draft);
      if (editingId) await updateGlossaryRule(editingId, {...payload, version: draft.version, expected_reference_revision: draft.reference_revision}, {headers: editSession.getMutationHeaders()});
      else await createGlossaryRule(payload);
      setEditingId(null); setDraft(empty); await load();
    } catch (err) {
      editSession.handleMutationError?.(err);
      const detail = err.data || err;
      if (detail.code === "version_conflict" && editingId) {
        try {
          const remote = (await listGlossaryRules()).find(item => item.id === editingId);
          if (remote) setConflict({base: editBase, local: draft, remote: editableRule(remote)});
          else showToast(t("apiError.not_found"), {type: "error"});
        } catch (loadError) {showToast(apiToast(loadError, t, {type: "error"}));}
        return;
      }
      const targets = (detail.conflicts || []).filter((item) => item.id && item.id !== editingId);
      if (detail.code === "glossary_rule_overlap" && targets.length) {
        setProposal(null);
        setMerge({targets, targetId: targets[0].id, returnFocusTo,
          ...(editingId ? {source: rules.find((item) => item.id === editingId), sourceEdit: payload} : {draft: payload})});
      } else showToast(apiToast(err, t, { type: "error" }));
    } finally { setBusy(false); }
  };
  const remove = async (rule) => {
    setBusy(true);
    try {
      if (rule.id === editingId) {
        if (editSession.state !== "editing") return;
        await deleteGlossaryRule(rule.id, rule.version, {expected_reference_revision: rule.reference_revision, headers: editSession.getMutationHeaders()});
        setEditingId(null); setDraft(empty);
      } else await withReferenceEditLock({resource_type: "glossary_rule", resource_id: String(rule.id)}, ({headers}) =>
        deleteGlossaryRule(rule.id, rule.version, {expected_reference_revision: rule.reference_revision, headers}));
      await load();
    }
    catch (err) { showToast(apiToast(err, t, { type: "error" })); }
    finally { setBusy(false); }
  };
  const beginEdit = (rule) => {
    if (busy || (editingId && !window.confirm(t("admin.glossary.unsavedChangesConfirm")))) return;
    setDraft(editableRule(rule)); setEditBase(editableRule(rule)); setConflict(null);
    setEditingId(rule.id);
  };
  const checkDraft = async (event) => {
    if (!event.currentTarget.form.reportValidity() || !query.trim() || previewBusy) return;
    setPreviewBusy(true);
    try { setRulePreview({key: previewKey, data: await previewGlossaryRule({...payloadFor(draft), query})}); }
    catch (err) { setRulePreview(null); showToast(apiToast(err, t, {type: "error"})); }
    finally { setPreviewBusy(false); }
  };
  const beginMerge = (source, returnFocusTo) => {
    const targets = rules.filter((item) => item.id !== source.id);
    if (!targets.length) return;
    setProposal(null); setMerge({source, targets, targetId: targets[0].id, returnFocusTo});
  };
  const mergeRequest = () => {
    const target = merge.targets.find((item) => item.id === merge.targetId);
    return {target_rule_id: target.id, target_version: proposal?.target.version ?? target.version,
      ...(merge.draft ? {draft: merge.draft} : {source_rule_id: merge.source.id,
        source_version: merge.sourceEdit ? merge.source.version : proposal?.source.version ?? merge.source.version,
        ...(merge.sourceEdit ? {source_edit: merge.sourceEdit} : {})})};
  };
  const previewMerge = async () => {
    setBusy(true);
    try { setProposal(await previewGlossaryRuleMerge(mergeRequest())); }
    catch (err) { mergeSession.handleMutationError?.(err); setProposal(null); showToast(apiToast(err, t, {type: "error"})); }
    finally { setBusy(false); }
  };
  const commitMerge = async () => {
    if (busy || mergeSession.state !== "editing" || !proposal) return;
    setBusy(true);
    try {
      await commitGlossaryRuleMerge({...mergeRequest(), preview_digest: proposal.digest, expected_revision: proposal.glossary_revision,
        expected_reference_revisions: Object.fromEntries([...(merge.draft ? [] : [merge.sourceEdit ? merge.source : proposal.source]), proposal.target].map(item => [item.id, item.reference_revision])),
        edit_sessions: mergeSession.getEditSessions()});
      setMerge(null); setProposal(null); setEditingId(null); setDraft(empty); await load();
    } catch (err) { setProposal(null); showToast(apiToast(err, t, {type: "error"})); }
    finally { setBusy(false); }
  };
  return <section className="glossary-rules" aria-label={t("admin.glossary.infotypeRuleTitle")}>
    <h3>{t("admin.glossary.infotypeRuleTitle")}</h3>
    <p className="muted">{t("admin.glossary.rulesHint")}</p>
    {loading ? <p className="muted">{t("common.loading")}</p> : <ul className="glossary-rule-list">{rules.map((rule) => <li key={rule.id}>
      <div className="glossary-rule-summary"><strong>{rule.name}</strong><code>{rangeText(rule)}</code>
        <span>{formatGlossaryRulePrefixes(rule.prefixes)}</span><small className="muted">{t(rule.enabled ? "admin.glossary.active" : "admin.glossary.disabled")}</small></div>
      {canManage && <div className="glossary-actions">
        <button type="button" className="modal-btn" disabled={busy} onClick={() => beginEdit(rule)}>{t("admin.glossary.editRule")}</button>
        <button type="button" className="modal-btn" disabled={busy || rules.length < 2} onClick={(event) => beginMerge(rule, event.currentTarget)}>{t("admin.glossary.mergeWith")}</button>
        <button type="button" className="modal-btn" disabled={busy} onClick={() => remove(rule)}>{t("admin.glossary.deleteRule")}</button>
      </div>}
    </li>)}</ul>}
    {canManage && <form onSubmit={save} className="glossary-rule-form" onInputCapture={editSession.markActivity} onPointerDownCapture={editSession.markActivity} onKeyDownCapture={editSession.markActivity}>
      {editingId && <ReferenceEditNotice session={editSession} onCopy={() => navigator.clipboard.writeText(JSON.stringify(draft, null, 2)).catch(() => {})} />}
      <fieldset className="reference-editor-fields" disabled={Boolean(busy || !writable || conflict || merge)}>
      <h4>{t(editingId ? "admin.glossary.editRule" : "admin.glossary.newRule")}</h4>
      <div className="glossary-rule-fields">
        <label className="glossary-field glossary-rule-name">{t("admin.glossary.ruleName")}<input required maxLength={128} disabled={busy} value={draft.name} onChange={(e) => setDraft({...draft, name: e.target.value})} /></label>
        <label className="glossary-field">{t("admin.glossary.ruleFrom")}<input inputMode="numeric" pattern="[0-9]{4}" maxLength={4} required disabled={busy} value={draft.number_from} onChange={(e) => setDraft({...draft, number_from: e.target.value})} /></label>
        <label className="glossary-field">{t("admin.glossary.ruleTo")}<input inputMode="numeric" pattern="[0-9]{4}" maxLength={4} required disabled={busy} value={draft.number_to} onChange={(e) => setDraft({...draft, number_to: e.target.value})} /></label>
        <label className="glossary-field glossary-rule-prefixes">{t("admin.glossary.rulePrefixes")}<input required disabled={busy} value={draft.prefixes} onChange={(e) => setDraft({...draft, prefixes: e.target.value})} /></label>
      </div>
      <div className="glossary-actions">
        <label className="glossary-check"><input type="checkbox" disabled={busy} checked={draft.enabled} onChange={(e) => setDraft({...draft, enabled: e.target.checked})} />{t("admin.glossary.enabled")}</label>
        <button className="modal-btn" disabled={busy}>{t(editingId ? "admin.glossary.saveRule" : "admin.glossary.addRule")}</button>
      </div>
      <details className="glossary-rule-preview">
        <summary>{t("admin.glossary.rulePreviewTitle")}</summary>
        <p className="muted">{t("admin.glossary.rulePreviewHint")}</p>
        <div className="glossary-inline-form">
          <input aria-label={t("admin.glossary.preview")} value={query} maxLength={8192} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); checkDraft(event); } }} placeholder={t("admin.glossary.previewPlaceholder")} />
          <button type="button" className="modal-btn" disabled={busy || previewBusy || !query.trim()} onClick={checkDraft}>{t("admin.glossary.check")}</button>
        </div>
        {rulePreview?.key === previewKey && <div className="glossary-preview-result" role="status">
          {rulePreview.data.matches.length ? <ul>{rulePreview.data.matches.map((item) => <li key={item.number}>
            <strong>{item.code}</strong>: {item.generated_forms.join(", ")}
          </li>)}</ul> : <p>{t("admin.glossary.noMatches")}</p>}
        </div>}
      </details>
      </fieldset>
      {editingId && <button type="button" className="modal-btn" disabled={busy} onClick={() => {setEditingId(null); setDraft(empty); setConflict(null);}}>{t("common.cancel")}</button>}
    </form>}
    {conflict && <ReferenceConflictDialog {...conflict} fields={RULE_FIELDS}
      labels={{name: t("admin.glossary.ruleName"), number_from: t("admin.glossary.ruleFrom"), number_to: t("admin.glossary.ruleTo"), prefixes: t("admin.glossary.rulePrefixes"), enabled: t("admin.glossary.enabled")}}
      onClose={() => setConflict(null)} onConfirm={value => {setDraft({...value, version: conflict.remote.version, reference_revision: conflict.remote.reference_revision}); setEditBase(conflict.remote); setConflict(null);}} />}
    {merge && <div ref={dialogRef} tabIndex={-1} className="modal-backdrop" role="dialog" aria-modal="true" aria-label={t("admin.glossary.mergeTitle")}>
      <div className="modal-card" onInputCapture={mergeSession.markActivity} onPointerDownCapture={mergeSession.markActivity} onKeyDownCapture={mergeSession.markActivity}>
        <h3>{t("admin.glossary.mergeTitle")}</h3>
        <ReferenceEditNotice session={mergeSession} onCopy={() => navigator.clipboard.writeText(JSON.stringify(mergeRequest(), null, 2)).catch(() => {})} />
        <p>{(merge.draft || merge.sourceEdit || merge.source).name}</p>
        <label>{t("admin.glossary.mergeWith")}<select disabled={busy} value={merge.targetId} onChange={(e) => {setProposal(null); setMerge({...merge, targetId: Number(e.target.value)});}}>
          {merge.targets.map((rule) => <option key={rule.id} value={rule.id}>{rule.name} ({rangeText(rule)})</option>)}
        </select></label>
        {proposal && <div role="status"><strong>{t("admin.glossary.mergeResult")}</strong><p>{proposal.target.name}: {rangeText(proposal)} · {formatGlossaryRulePrefixes(proposal.merged_prefixes)}</p></div>}
        <div className="modal-actions">
          <button type="button" className="modal-btn" disabled={busy} onClick={() => {setMerge(null); setProposal(null);}}>{t("common.cancel")}</button>
          <button type="button" className="modal-btn" disabled={busy} onClick={previewMerge}>{t("admin.glossary.mergePreview")}</button>
          {proposal && <button type="button" className="modal-btn" disabled={busy || mergeSession.state !== "editing"} onClick={commitMerge}>{t("admin.glossary.mergeCommit")}</button>}
        </div>
      </div>
    </div>}
  </section>;
}
