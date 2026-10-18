"use client";

import { apiToast } from "@/lib/apiToast.mjs";

// Редактор runtime-override UI-словаря локали (Этап 7 фаза C).
// Правка = textarea с JSON; при первом открытии префилдится активным override.
// ЗАЩИТА ПРАВОК: после первого ручного изменения (isDirty) повторные загрузки
// с сервера («Повторить») НЕ перезаписывают textarea — обновляется только статус
// ошибки/метаданные. Сбросить к серверному содержимому можно перезагрузкой раздела.
// Применение — ПОЛНАЯ замена override-словаря.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  friendlyApiError,
  getUiDictionaryAdmin,
  importUiDictionary,
  rollbackUiDictionary,
  uiDictionaryHistory,
} from "@/lib/api";
import { useReferenceEditSession } from "@/hooks/useReferenceEditSession";
import ReferenceEditNotice from "./ReferenceEditNotice";
import ReferenceDictionaryConflictDialog from "./ReferenceDictionaryConflictDialog";
import Modal from "./Modal";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";
import { downloadDict, effectiveDictionary, exportFilename, enSourceDictionary } from "@/lib/uiDictExport.mjs";

export default function UiDictionaryEditor({ locale, onDirtyChange, onBusyChange }) {
  const { t } = useI18n();
  const { showToast } = useToast();

  // Guard от передачи объекта вместо строки (инцидент «[object Object]», P0).
  const code = typeof locale === "string" && locale.trim() ? locale.trim() : null;

  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const [result, setResult] = useState(null); // ответ import: {errors, applied, preview:{total,added,...}}
  const [history, setHistory] = useState([]);
  const [isNew, setIsNew] = useState(false); // активного override ещё нет
  const [activeData, setActiveData] = useState(null); // данные активного override (для экспорта)
  const [loadError, setLoadError] = useState(null); // текст ошибки загрузки (или null)
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [comparison, setComparison] = useState(null);
  const [rollbackPreview, setRollbackPreview] = useState(null);
  const baseline = useRef(null);
  const session = useReferenceEditSession({resource: {resource_type: "ui_dictionary", resource_id: code || "invalid"},
    enabled: Boolean(code), onLost: () => {setResult(null); setRollbackPreview(null);},
    onAcquired: async (_status, isCurrent) => {
      const fresh = await getUiDictionaryAdmin(code);
      if (!isCurrent()) return;
      setResult(null); setRollbackPreview(null);
      setActiveData(fresh.data || null); setIsNew(fresh.data == null);
      if (dirty) {
        if (baseline.current?.reference_revision !== fresh.reference_revision) {
          try {openComparison(fresh, JSON.parse(text || "{}"));}
          catch { /* Invalid JSON remains a draft; preview requires valid JSON and compares again. */ }
        }
      } else {
        baseline.current = fresh;
        const value = fresh.data == null ? "" : JSON.stringify(fresh.data, null, 2);
        textRef.current = value; setText(value); setNote(fresh.note || "");
      }
    }});
  const writable = session.state === "editing" && !busy && !comparison;
  useEffect(() => {onDirtyChange?.(dirty);}, [dirty, onDirtyChange]);
  useEffect(() => {onBusyChange?.(busy);}, [busy, onBusyChange]);
  useEffect(() => () => {onDirtyChange?.(false); onBusyChange?.(false);}, [onDirtyChange, onBusyChange]);

  // Флаг «пользователь уже правил JSON» — реф, чтобы не перезаписывать при retry.
  const dirtyRef = useRef(false);
  const textRef = useRef("");

  const onTextChange = (v) => {
    textRef.current = v;
    dirtyRef.current = true; setDirty(true); setResult(null);
    setText(v);
  };

  const loadHistory = useCallback(async () => {
    if (!code) return;
    try {
      setHistory(await uiDictionaryHistory(code));
    } catch {
      // молча
    }
  }, [code]);

  // Загрузка активного override. textarea заполняется ТОЛЬКО если пользователь ещё
  // не начал править (dirtyRef=false) — иначе сетевой retry уничтожил бы правки.
  const loadActive = useCallback(async () => {
    if (!code) return false;
    setLoading(true);
    setLoadError(null);
    try {
      const active = await getUiDictionaryAdmin(code);
      const hasData = active && active.data != null;
      setIsNew(!hasData);
      setActiveData(hasData ? active.data : null);
      if (!dirtyRef.current) {
        baseline.current = active;
        setText(hasData ? JSON.stringify(active.data, null, 2) : "");
        textRef.current = hasData ? JSON.stringify(active.data, null, 2) : "";
        setNote(active?.note || "");
      }
      return true;
    } catch (err) {
      setLoadError(friendlyApiError(err, t));
      return false;
    } finally {
      setLoading(false);
    }
  }, [code]);

  useEffect(() => {
    loadActive();
    loadHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code]);

  // Ошибка разбора не заворачивается в русский литерал: вызывающий и так
  // показывает её через admin.uictl.invalidJson, а двойная обёртка приводила
  // англоязычного пользователя к «Invalid JSON: Некорректный JSON: …».
  const parseJson = () => {
    const parsed = JSON.parse(textRef.current || "{}");
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error(t("admin.uictl.notAnObject"));
    }
    return parsed;
  };

  const openComparison = (remote, data) => setComparison({base: baseline.current?.data || {},
    local: data, remote: remote.data || {}, snapshot: remote});
  const reportWriteError = err => {
    session.handleMutationError?.(err);
    if (["version_conflict", "reference_preview_stale"].includes(err.code || err.data?.code)) {
      setResult(null); setRollbackPreview(null);
    }
    showToast(apiToast(err, t, {type: "error"}));
  };
  const onPreview = async () => {
    if (!code || busy) return;
    let data;
    try {data = parseJson();}
    catch (err) {showToast(t("admin.uictl.invalidJson", {error: err.message}), {type: "error"}); return;}
    setBusy(true);
    try {
      const remote = await getUiDictionaryAdmin(code);
      if (!baseline.current || baseline.current.reference_revision !== remote.reference_revision) {
        setResult(null); openComparison(remote, data); return;
      }
      const request = {data, note};
      const result = await importUiDictionary(code, {...request, confirm: false});
      if (result.errors?.length) {setResult(result); return;}
      if (result.reference_revision !== baseline.current.reference_revision) {
        openComparison(await getUiDictionaryAdmin(code), data); setResult(null); return;
      }
      setResult({...result, request});
    } catch (err) {reportWriteError(err);}
    finally {setBusy(false);}
  };
  const onApply = async () => {
    if (!code || !writable || !result?.request || !result.preview_digest || result.errors?.length) return;
    setBusy(true);
    try {
      const saved = await importUiDictionary(code, {...result.request, confirm: true,
        expected_reference_revision: result.reference_revision, expected_active_version: result.active_version,
        preview_digest: result.preview_digest, headers: session.getMutationHeaders()});
      if (saved.applied) {
        showToast(t("admin.uictl.applied"), {type: "success"});
        setResult(null); dirtyRef.current = false; setDirty(false);
        await loadActive(); await loadHistory();
      } else setResult(saved);
    } catch (err) {reportWriteError(err);}
    finally {setBusy(false);}
  };
  const onRollback = async entry => {
    if (!writable) return;
    setBusy(true);
    try {setRollbackPreview({entry, response: await rollbackUiDictionary(code, entry.id, {confirm: false})});}
    catch (err) {reportWriteError(err);}
    finally {setBusy(false);}
  };
  const applyRollback = async () => {
    if (!writable || !rollbackPreview) return;
    setBusy(true);
    try {
      const {entry, response} = rollbackPreview;
      const saved = await rollbackUiDictionary(code, entry.id, {confirm: true,
        expected_reference_revision: response.reference_revision, expected_active_version: response.active_version,
        preview_digest: response.preview_digest, headers: session.getMutationHeaders()});
      if (saved.applied) {
        setResult(null); setRollbackPreview(null);
        showToast(t("admin.uictl.rolledBack"), {type: "success"});
        await loadActive(); await loadHistory();
      }
    } catch (err) {reportWriteError(err);}
    finally {setBusy(false);}
  };

  const previewErrors = result?.errors && result.errors.length ? result.errors : null;

  if (!code) {
    return <div className="uictl-errors">{t("admin.languages.invalidLocale")}</div>;
  }

  return (
    <div className="uictl-editor" onInputCapture={session.markActivity} onKeyDownCapture={session.markActivity} onPointerDownCapture={session.markActivity}>
      <ReferenceEditNotice session={session} onCopy={() => navigator.clipboard.writeText(`${text}\n${note}`).catch(() => {})} />
      {isNew ? (
        <p className="muted">{t("admin.uictl.newOverride")}</p>
      ) : (
        <p className="muted">{t("admin.uictl.editingCurrent")}</p>
      )}
      <p className="uictl-warning">{t("admin.uictl.replaceSemantics")}</p>

      <div className="uictl-exports">
        <h4>{t("admin.uictl.samplesTitle")}</h4>
        <div className="uictl-exports-btns">
          <button
            className="modal-btn"
            onClick={() => downloadDict(effectiveDictionary(code, activeData), exportFilename(code, "effective"))}
          >
            {t("admin.uictl.downloadEffective", { locale: code })}
          </button>
          <button
            className="modal-btn"
            onClick={() => downloadDict(enSourceDictionary(), exportFilename(code, "en-source"))}
          >
            {t("admin.uictl.downloadEnSource")}
          </button>
          <button
            className="modal-btn"
            disabled={!activeData}
            title={!activeData ? t("admin.uictl.noOverrideYet") : undefined}
            onClick={() => downloadDict(activeData || {}, exportFilename(code, "override"))}
          >
            {t("admin.uictl.downloadOverride")}
          </button>
        </div>
        <p className="muted">{t("admin.uictl.effectiveHint", { locale: code })}</p>
        <p className="muted">{t("admin.uictl.enSourceHint")}</p>
      </div>

      {loadError ? (
        <div className="uictl-errors">
          <span>{loadError}</span>
          <button
            className="modal-btn uictl-retry"
            disabled={loading}
            onClick={() => loadActive()}
          >
            {t("admin.uictl.retry")}
          </button>
        </div>
      ) : null}

      <textarea
        className="uictl-textarea"
        value={text}
        onChange={(e) => onTextChange(e.target.value)} readOnly={!writable || loading}
        rows={10}
        placeholder={t("admin.uictl.placeholder")}
        spellCheck={false}
      />
      <input
        className="uictl-note"
        value={note}
        readOnly={!writable || loading} onChange={(e) => {setNote(e.target.value); dirtyRef.current = true; setDirty(true); setResult(null);}}
        placeholder={t("admin.uictl.notePlaceholder")}
      />
      <div className="uictl-controls">
        <button className="modal-btn" disabled={busy} onClick={onPreview}>
          {t("admin.uictl.preview")}
        </button>
        <button className="modal-btn" disabled={!writable || !result?.request || Boolean(previewErrors)} onClick={onApply}>
          {t("admin.uictl.apply")}
        </button>
      </div>

      {previewErrors && (
        <div className="uictl-errors">
          {previewErrors.map((e, i) => (
            <div key={i}>✗ {e}</div>
          ))}
        </div>
      )}
      {result?.preview && !previewErrors && (
        <div className="stopwords-preview">
          <div>{t("admin.uictl.total", { count: result.preview.total })}</div>
          <div>{t("admin.uictl.added", { count: result.preview.added.length })}</div>
          <div>{t("admin.uictl.removed", { count: result.preview.removed.length })}</div>
          <div>{t("admin.uictl.unchanged", { count: result.preview.unchanged.length })}</div>
        </div>
      )}

      <div className="uictl-history">
        <h4>{t("admin.uictl.history")}</h4>
        {history.length === 0 ? (
          <p className="muted">{t("admin.uictl.historyEmpty")}</p>
        ) : (
          <ul className="job-list">
            {history.map((entry) => (
              <li key={entry.id} className="job-item">
                <div className="job-main">
                  v{entry.version} · {entry.uploaded_by ?? "—"} ·{" "}
                  {new Date(entry.created_at).toLocaleString()} · {entry.key_count}{" "}
                  {t("admin.uictl.keys")}
                  {entry.note ? ` · ${entry.note}` : ""}
                </div>
                <div className="job-actions">
                  <button className="modal-btn" disabled={!writable} onClick={() => onRollback(entry)}>
                    {t("admin.uictl.rollback")}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
      {comparison && <ReferenceDictionaryConflictDialog {...comparison} onClose={() => setComparison(null)}
        onConfirm={value => {baseline.current = comparison.snapshot; onTextChange(JSON.stringify(value, null, 2)); setComparison(null);}} />}
      {rollbackPreview && <Modal title={t("admin.uictl.rollbackConfirm", {version: rollbackPreview.entry.version})}
        onClose={() => {if (!busy) setRollbackPreview(null);}} footer={<>
          <button type="button" className="modal-btn" disabled={busy} onClick={() => setRollbackPreview(null)}>{t("common.cancel")}</button>
          <button type="button" className="modal-btn" disabled={!writable} onClick={applyRollback}>{t("admin.uictl.rollback")}</button>
        </>}>
        <pre className="reference-dictionary-preview">{JSON.stringify(rollbackPreview.response.target_data, null, 2)}</pre>
      </Modal>}
    </div>
  );
}
