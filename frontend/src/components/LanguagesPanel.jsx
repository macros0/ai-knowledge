"use client";

import { useCallback, useEffect, useState } from "react";
import {
  activateLocale,
  addStopword,
  deleteStopword,
  disableLocale,
  importStopwords,
  listLocales,
  listStopwords,
  createLocale,
  probeStopwords,
  rollbackStopwords,
  stopwordsHistory,
} from "@/lib/api";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";
import { canActivate, canDisable } from "@/lib/localeActions.mjs";
import UiDictionaryEditor from "./UiDictionaryEditor";
import BackfillControl from "./BackfillControl";

const KINDS = ["bm25", "marker"];

function StatusBadge({ status }) {
  const { t } = useI18n();
  return (
    <span className={`job-status ${status}`}>
      {t(`admin.languages.status.${status}`)}
    </span>
  );
}

function StopwordsEditor({ locale }) {
  const { t } = useI18n();
  const { showToast } = useToast();

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

  const loadWords = useCallback(async () => {
    try {
      const list = await listStopwords(locale, kind);
      setWords({ ...words, [kind]: list });
    } catch (err) {
      showToast(err.message, { type: "error" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locale, kind]);

  const loadHistory = useCallback(async () => {
    try {
      setHistory(await stopwordsHistory(locale));
    } catch {
      // молча
    }
  }, [locale]);

  useEffect(() => {
    loadWords();
    loadHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locale, kind]);

  const splitLines = (text) =>
    text
      .split("\n")
      .map((w) => w.trim())
      .filter(Boolean);

  const act = async (fn, okMsg) => {
    setBusy(true);
    try {
      await fn();
      showToast(okMsg);
      await loadWords();
      await loadHistory();
    } catch (err) {
      showToast(err.message, { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  const onAdd = () => {
    if (!newWord.trim()) return;
    act(
      () => addStopword(locale, { word: newWord.trim(), kind }),
      t("admin.stopwords.imported")
    );
    setNewWord("");
  };

  const onPreview = async () => {
    try {
      const res = await importStopwords(locale, {
        words: splitLines(importText),
        kind,
        mode: importMode,
        confirm: false,
      });
      setPreview(res);
    } catch (err) {
      showToast(err.message, { type: "error" });
    }
  };

  const onApply = () =>
    act(
      () =>
        importStopwords(locale, {
          words: splitLines(importText),
          kind,
          mode: importMode,
          confirm: true,
        }),
      t("admin.stopwords.imported")
    ).then(() => setPreview(null));

  const onRollback = (entry) => {
    if (
      !window.confirm(
        t("admin.stopwords.rollbackConfirm", { time: new Date(entry.created_at).toLocaleString() })
      )
    )
      return;
    act(() => rollbackStopwords(locale, entry.id), t("admin.stopwords.rolledBack"));
  };

  const onProbe = async () => {
    try {
      setProbeResults(await probeStopwords(locale, splitLines(probeText)));
    } catch (err) {
      showToast(err.message, { type: "error" });
    }
  };

  const currentWords = words[kind] ?? [];

  return (
    <div className="stopwords-editor">
      <div className="stopwords-toolbar">
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {t(k === "bm25" ? "admin.stopwords.kindBm25" : "admin.stopwords.kindMarker")}
            </option>
          ))}
        </select>
        <input
          value={newWord}
          onChange={(e) => setNewWord(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && onAdd()}
          placeholder={t("admin.stopwords.addPlaceholder")}
        />
        <button className="modal-btn" disabled={busy} onClick={onAdd}>
          {t("admin.stopwords.add")}
        </button>
      </div>

      <p className="muted">{t("admin.stopwords.warningFrozen")}</p>

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
                disabled={busy}
                onClick={() =>
                  act(
                    () => deleteStopword(locale, w.word, kind),
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
          value={importText}
          onChange={(e) => setImportText(e.target.value)}
          placeholder={t("admin.stopwords.importPlaceholder")}
          rows={4}
        />
        <div className="stopwords-import-controls">
          <label>
            {t("admin.stopwords.mode")}:{" "}
            <select value={importMode} onChange={(e) => setImportMode(e.target.value)}>
              <option value="merge">{t("admin.stopwords.merge")}</option>
              <option value="replace">{t("admin.stopwords.replace")}</option>
            </select>
          </label>
          <button className="modal-btn" disabled={busy} onClick={onPreview}>
            {t("admin.stopwords.importPreview")}
          </button>
          <button className="modal-btn" disabled={busy || !preview} onClick={onApply}>
            {t("admin.stopwords.importApply")}
          </button>
        </div>
        {preview && (
          <div className="stopwords-preview">
            <div>{t("admin.stopwords.added", { count: preview.added.length })}</div>
            <div>{t("admin.stopwords.removed", { count: preview.removed.length })}</div>
            <div>{t("admin.stopwords.unchanged", { count: preview.unchanged.length })}</div>
            <div>{t("admin.stopwords.totalAfter", { count: preview.total_after })}</div>
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
                    disabled={busy}
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
    </div>
  );
}

export default function LanguagesPanel() {
  const { t } = useI18n();
  const { showToast } = useToast();

  const [locales, setLocales] = useState([]);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  // Активная секция под таблицей: {code, tool: 'stopwords'|'uictl'|'backfill'} | null.
  const [activeTool, setActiveTool] = useState(null);

  const toggleTool = (loc, tool) =>
    setActiveTool(activeTool && activeTool.code === loc && activeTool.tool === tool ? null : { code: loc, tool });

  const load = useCallback(async () => {
    try {
      setLocales(await listLocales());
    } catch (err) {
      showToast(t("admin.languages.loadError", { message: err.message }), { type: "error" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onCreate = async () => {
    if (!code.trim() || !name.trim()) return;
    try {
      await createLocale(code.trim(), name.trim());
      showToast(t("admin.languages.created", { code: code.trim() }));
      setCode("");
      setName("");
      await load();
    } catch (err) {
      showToast(t("admin.languages.createError", { message: err.message }), {
        type: "error",
      });
    }
  };

  const onActivate = async (c) => {
    try {
      await activateLocale(c);
      showToast(t("admin.languages.activated"));
      await load();
    } catch (err) {
      showToast(t("admin.languages.actError", { message: err.message }), { type: "error" });
    }
  };

  const onDisable = async (c) => {
    try {
      await disableLocale(c);
      showToast(t("admin.languages.disabled"));
      await load();
    } catch (err) {
      showToast(t("admin.languages.actError", { message: err.message }), { type: "error" });
    }
  };

  return (
    <section className="panel admin-panel">
      <div className="panel-head">
        <h2>{t("admin.languages.title")}</h2>
      </div>

      <div className="language-add-form">
        <input
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder={t("admin.languages.codePlaceholder")}
        />
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && onCreate()}
          placeholder={t("admin.languages.namePlaceholder")}
        />
        <button className="modal-btn" onClick={onCreate}>
          {t("admin.languages.addBtn")}
        </button>
      </div>

      <table className="language-table">
        <thead>
          <tr>
            <th>{t("admin.languages.colCode")}</th>
            <th>{t("admin.languages.colName")}</th>
            <th>{t("admin.languages.colStatus")}</th>
            <th>{t("admin.languages.colStopwords")}</th>
            <th>{t("admin.languages.actions")}</th>
          </tr>
        </thead>
        <tbody>
          {locales.map((loc) => (
            <tr key={loc.code}>
              <td>
                <strong>{loc.code}</strong>
              </td>
              <td>{loc.name}</td>
              <td>
                <StatusBadge status={loc.status} />
              </td>
              <td>
                {loc.stopwords_bm25_count} / {loc.stopwords_marker_count}
              </td>
              <td>
                <button
                  className="modal-btn"
                  onClick={() => toggleTool(loc, "stopwords")}
                  title={t("admin.languages.editStopwords")}
                >
                  {t("admin.languages.editStopwords")}
                </button>
                <button
                  className="modal-btn"
                  onClick={() => toggleTool(loc, "uictl")}
                  title={t("admin.languages.uictl")}
                >
                  {t("admin.languages.uictl")}
                </button>
                <button
                  className="modal-btn"
                  onClick={() => toggleTool(loc, "backfill")}
                  title={t("admin.languages.backfill")}
                >
                  {t("admin.languages.backfill")}
                </button>
                {canActivate(loc.status) && (
                  <button className="modal-btn" onClick={() => onActivate(loc.code)}>
                    {t("admin.languages.activate")}
                  </button>
                )}
                {canDisable(loc.status, loc.code) && (
                  <button className="modal-btn" onClick={() => onDisable(loc.code)}>
                    {t("admin.languages.disable")}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {activeTool && (
        <div className="stopwords-editor-wrap">
          {activeTool.tool === "stopwords" && (
            <>
              <h3>{t("admin.stopwords.title", { code: activeTool.code })}</h3>
              <StopwordsEditor locale={activeTool.code} />
            </>
          )}
          {activeTool.tool === "uictl" && (
            <>
              <h3>{t("admin.uictl.title", { code: activeTool.code })}</h3>
              <UiDictionaryEditor locale={activeTool.code} />
            </>
          )}
          {activeTool.tool === "backfill" && (
            <>
              <h3>{t("admin.backfill.title", { code: activeTool.code })}</h3>
              <BackfillControl locale={activeTool.code} />
            </>
          )}
        </div>
      )}
    </section>
  );
}
