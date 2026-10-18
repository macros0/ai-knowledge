"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiToast } from "@/lib/apiToast.mjs";
import {addStopword, deleteStopword, importStopwords, getStopwordSet, probeStopwords, rollbackStopwords, stopwordsHistory} from "@/lib/api";
import { useReferenceEditSession } from "@/hooks/useReferenceEditSession";
import ReferenceEditNotice from "./ReferenceEditNotice";
import Modal from "./Modal";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";
const KINDS = ["bm25", "marker"];

export default function StopwordsEditor({ locale, onDirtyChange, onBusyChange }) {
  const { t } = useI18n();
  const { showToast } = useToast();

  // Нормализация локали: guard от передачи объекта (инцидент «[object Object]»,
  // Этап 7 P0) — невалидная локаль даёт явную inline-ошибку, а не загадочный 404.
  const code = typeof locale === "string" && locale.trim() ? locale.trim() : null;

  const [words, setWords] = useState({});
  const [kind, setKind] = useState("bm25");
  const [newWord, setNewWord] = useState("");
  const [importText, setImportText] = useState("");
  const [importMode, setImportMode] = useState("merge");
  const [preview, setPreview] = useState(null);
  const [history, setHistory] = useState([]);
  const [probeText, setProbeText] = useState("");
  const [probeResults, setProbeResults] = useState(null);
  const [busy, setBusy] = useState(false);
  const [revisions, setRevisions] = useState({});
  const [rollbackPreview, setRollbackPreview] = useState(null);
  const [stalePreview, setStalePreview] = useState(false);
  const loadSequence = useRef(0);
  const session = useReferenceEditSession({resource: {resource_type: "stopword_set", resource_id: code || "invalid", kind},
    enabled: Boolean(code), onLost: () => {setPreview(null); setRollbackPreview(null);},
    onAcquired: async (_status, isCurrent) => {
      const fresh = await getStopwordSet(code, kind);
      if (!isCurrent()) return;
      setWords(previous => ({...previous, [kind]: fresh.words}));
      setRevisions(previous => ({...previous, ...fresh.reference_revisions}));
      setPreview(null); setRollbackPreview(null);
    }});
  const writable = session.state === "editing" && !busy && Number.isInteger(revisions[kind]);
  const dirty = Boolean(newWord.trim() || importText.trim() || preview || rollbackPreview);
  useEffect(() => {onDirtyChange?.(dirty);}, [dirty, onDirtyChange]);
  useEffect(() => {onBusyChange?.(busy);}, [busy, onBusyChange]);
  useEffect(() => () => {onDirtyChange?.(false); onBusyChange?.(false);}, [onDirtyChange, onBusyChange]);

  const currentLocaleRef = useRef(code);
  useEffect(() => {
    currentLocaleRef.current = code;
  }, [code]);

  const loadWords = useCallback(async () => {
    if (!code) return;
    const requested = code;
    try {
      const sequence = ++loadSequence.current;
      const snapshot = await getStopwordSet(code, kind);
      if (currentLocaleRef.current !== requested || sequence !== loadSequence.current) return;
      setWords(prev => ({...prev, [kind]: snapshot.words}));
      setRevisions(prev => ({...prev, ...snapshot.reference_revisions}));
    } catch (err) {
      showToast(apiToast(err, t, { type: "error" }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code, kind]);

  const loadHistory = useCallback(async () => {
    if (!code) return;
    try {
      setHistory(await stopwordsHistory(code));
    } catch {
      // молча
    }
  }, [code]);

  useEffect(() => {
    loadWords();
    loadHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code, kind]);

  const splitLines = (text) =>
    text
      .split("\n")
      .map((w) => w.trim())
      .filter(Boolean);

  const act = async (fn, okMsg) => {
    if (!writable) return false;
    setBusy(true);
    try {
      const result = await fn();
      if (result?.applied === false) return false;
      setPreview(null); setStalePreview(false);
      showToast(okMsg);
      await loadWords(); await loadHistory();
      return true;
    } catch (err) {
      session.handleMutationError?.(err);
      if (["version_conflict", "reference_preview_stale"].includes(err.code || err.data?.code)) {
        setPreview(null); setRollbackPreview(null); setStalePreview(true);
        await loadWords();
        showToast(t("reference.previewChanged"), {type: "error"});
      } else showToast(apiToast(err, t, {type: "error"}));
      return false;
    } finally {setBusy(false);}
  };
  const onAdd = async () => {
    if (!newWord.trim() || !code || !writable) return;
    const saved = await act(() => addStopword(code, {word: newWord.trim(), kind,
      expected_reference_revision: revisions[kind], headers: session.getMutationHeaders()}), t("admin.stopwords.imported"));
    if (saved) setNewWord("");
  };

  const onPreview = async () => {
    if (!code || busy) return;
    const request = {words: splitLines(importText), kind, mode: importMode};
    setBusy(true);
    try {
      const res = await importStopwords(code, {
        words: splitLines(importText),
        kind,
        mode: importMode,
        confirm: false,
      });
      setPreview({...res, request}); setStalePreview(false);
    } catch (err) {
      showToast(apiToast(err, t, { type: "error" }));
    } finally {setBusy(false);}
  };

  // Пустой replace (mode=replace + words=[]) — деструктивная очистка набора:
  // сервер требует confirm_empty_replace; показываем отдельное явное подтверждение.
  const isEmptyReplace =
    importMode === "replace" && splitLines(importText).length === 0;
  const emptyReplaceCount = preview?.removed?.length ?? 0;

  const doApply = async confirmEmpty => {
    if (!writable || !preview?.request || preview.request.kind !== kind) return;
    const saved = await act(() => importStopwords(code, {...preview.request, confirm: true,
      confirm_empty_replace: confirmEmpty, expected_reference_revision: preview.reference_revision,
      preview_digest: preview.preview_digest, headers: session.getMutationHeaders()}), t("admin.stopwords.imported"));
    if (saved) setPreview(null);
  };
  const onApply = () => {
    if (!writable || !preview?.request) return;
    if (isEmptyReplace && emptyReplaceCount > 0 && !window.confirm(t("admin.stopwords.emptyReplaceConfirm", {
      count: emptyReplaceCount, kind, locale: code}))) return;
    return doApply(isEmptyReplace && emptyReplaceCount > 0);
  };
  const onRollback = async entry => {
    if (!code || !writable || entry.kind !== kind) return;
    setBusy(true);
    try {setRollbackPreview({entry, response: await rollbackStopwords(code, entry.id, {confirm: false, kind})});}
    catch (err) {showToast(apiToast(err, t, {type: "error"}));}
    finally {setBusy(false);}
  };
  const applyRollback = async () => {
    if (!writable || !rollbackPreview) return;
    const {entry, response} = rollbackPreview;
    const saved = await act(() => rollbackStopwords(code, entry.id, {confirm: true, kind,
      expected_reference_revision: response.reference_revision, preview_digest: response.preview_digest,
      headers: session.getMutationHeaders()}), t("admin.stopwords.rolledBack"));
    if (saved) setRollbackPreview(null);
  };
  const changeKind = next => {
    if (busy || (dirty && !window.confirm(t("admin.glossary.unsavedChangesConfirm")))) return;
    setPreview(null); setRollbackPreview(null); setStalePreview(false);
    setNewWord(""); setImportText(""); setKind(next);
  };

  const onProbe = async () => {
    if (!code) return;
    try {
      setProbeResults(await probeStopwords(code, splitLines(probeText)));
    } catch (err) {
      showToast(apiToast(err, t, { type: "error" }));
    }
  };

  const currentWords = words[kind] ?? [];

  if (!code) {
    return <div className="uictl-errors">{t("admin.languages.invalidLocale")}</div>;
  }

  return (
    <div className="stopwords-editor" onInputCapture={session.markActivity} onKeyDownCapture={session.markActivity} onPointerDownCapture={session.markActivity}>
      <ReferenceEditNotice session={session} onCopy={() => navigator.clipboard.writeText(`${kind}\n${newWord}\n${importText}`).catch(() => {})} />
      {stalePreview && <p role="alert">{t("reference.previewChanged")}</p>}
      <div className="stopwords-toolbar">
        <select value={kind} disabled={busy} onChange={(e) => changeKind(e.target.value)}>
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {t(k === "bm25" ? "admin.stopwords.kindBm25" : "admin.stopwords.kindMarker")}
            </option>
          ))}
        </select>
        <input
          value={newWord} readOnly={!writable}
          onChange={(e) => setNewWord(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && onAdd()}
          placeholder={t("admin.stopwords.addPlaceholder")}
        />
        <button className="modal-btn" disabled={!writable} onClick={onAdd}>
          {t("admin.stopwords.add")}
        </button>
      </div>

      <p className="muted">{t("admin.stopwords.warningFrozen")}</p>

      {code.split("-")[0] === "de" && (
        <p className="muted">{t("admin.stopwords.hintGermanSharpS")}</p>
      )}

      {currentWords.length === 0 ? (
        <p className="muted">{t("admin.stopwords.noWords")}</p>
      ) : (
        <div className="stopword-chips">
          {currentWords.map((w) => (
            <span key={w.word} className="stopword-chip">
              {w.word}
              <button
                className="chip-x"
                aria-label={t("admin.stopwords.deleteAria", { word: w.word })}
                disabled={!writable}
                onClick={() =>
                  act(
                    () => deleteStopword(code, w.word, kind, {expected_reference_revision: revisions[kind], headers: session.getMutationHeaders()}),
                    t("admin.stopwords.imported")
                  )
                }
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="stopwords-import">
        <textarea
          value={importText} readOnly={!writable}
          onChange={(e) => {setImportText(e.target.value); setPreview(null);}}
          placeholder={t("admin.stopwords.importPlaceholder")}
          rows={4}
        />
        <div className="stopwords-import-controls">
          <label>
            {t("admin.stopwords.mode")}:{" "}
            <select value={importMode} disabled={!writable} onChange={(e) => {setImportMode(e.target.value); setPreview(null);}}>
              <option value="merge">{t("admin.stopwords.merge")}</option>
              <option value="replace">{t("admin.stopwords.replace")}</option>
            </select>
          </label>
          <button className="modal-btn" disabled={busy} onClick={onPreview}>
            {t("admin.stopwords.importPreview")}
          </button>
          <button className="modal-btn" disabled={!writable || !preview?.request} onClick={onApply}>
            {isEmptyReplace && emptyReplaceCount > 0
              ? t("admin.stopwords.emptyReplaceBtn", { count: emptyReplaceCount })
              : t("admin.stopwords.importApply")}
          </button>
        </div>
        {preview && (
          <div className="stopwords-preview">
            <div>{t("admin.stopwords.added", { count: preview.added.length })}</div>
            <div>{t("admin.stopwords.removed", { count: preview.removed.length })}</div>
            <div>{t("admin.stopwords.unchanged", { count: preview.unchanged.length })}</div>
            <div>{t("admin.stopwords.totalAfter", { count: preview.total_after })}</div>
            <details><summary>{t("reference.batchDetails")}</summary><pre className="reference-dictionary-preview">{`+ ${preview.added.join(", ")}\n− ${preview.removed.join(", ")}`}</pre></details>
          </div>
        )}
      </div>

      <div className="stopwords-history">
        <h4>{t("admin.stopwords.history")}</h4>
        {history.length === 0 ? (
          <p className="muted">{t("admin.stopwords.historyEmpty")}</p>
        ) : (
          <ul className="job-list">
            {history.map((entry) => (
              <li key={entry.id} className="job-item">
                <div className="job-main">
                  #{entry.id} · {entry.username ?? "—"} ·{" "}
                  {new Date(entry.created_at).toLocaleString()} · {entry.kind}
                </div>
                {entry.meta && (
                  <div className="job-meta">
                    +{entry.meta.added ?? 0} / −{entry.meta.removed ?? 0}
                  </div>
                )}
                <div className="job-actions">
                  <button
                    className="modal-btn"
                    disabled={!writable || entry.kind !== kind}
                    onClick={() => onRollback(entry)}
                  >
                    {t("admin.stopwords.rollback")}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="stopwords-probe">
        <h4>{t("admin.stopwords.probe")}</h4>
        <textarea
          value={probeText}
          onChange={(e) => setProbeText(e.target.value)}
          placeholder={t("admin.stopwords.probePlaceholder")}
          rows={3}
        />
        <button className="modal-btn" disabled={busy} onClick={onProbe}>
          {t("admin.stopwords.probeRun")}
        </button>
        {probeResults && (
          <div className="stopwords-probe-results">
            {probeResults.length === 0 ? (
              <p className="muted">{t("admin.stopwords.probeEmpty")}</p>
            ) : (
              probeResults.map((r) => (
                <div key={r.query} className="probe-item">
                  <strong>{r.query}</strong>
                  {r.hits.length === 0 ? (
                    <div className="muted">{t("admin.stopwords.probeEmpty")}</div>
                  ) : (
                    <ol>
                      {r.hits.map((h, i) => (
                        <li key={i}>
                          {h.title} <span className="muted">({h.score})</span>
                        </li>
                      ))}
                    </ol>
                  )}
                </div>
              ))
            )}
          </div>
        )}
      </div>
      {rollbackPreview && <Modal title={t("admin.stopwords.rollback")} onClose={() => {if (!busy) setRollbackPreview(null);}} footer={<>
        <button type="button" className="modal-btn" disabled={busy} onClick={() => setRollbackPreview(null)}>{t("common.cancel")}</button>
        <button type="button" className="modal-btn" disabled={!writable} onClick={applyRollback}>{t("admin.stopwords.rollback")}</button>
      </>}>
        <pre className="reference-dictionary-preview">{`+ ${rollbackPreview.response.added.join(", ")}\n− ${rollbackPreview.response.removed.join(", ")}`}</pre>
      </Modal>}
    </div>
  );
}
