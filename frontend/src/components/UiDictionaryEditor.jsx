"use client";

// Редактор runtime-override UI-словаря локали (Этап 7 фаза C).
// Правка = textarea с JSON; при открытии префилдится активным override (редактирование
// текущего), при отсутствии — пустая textarea (НЕ подставляется versioned-словарь,
// чтобы не превратить override в огромный снапшот). Применение — ПОЛНАЯ замена
// override-словаря: ключи вне textarea начнут браться из versioned-словаря/ru.

import { useCallback, useEffect, useState } from "react";
import {
  getUiDictionaryAdmin,
  importUiDictionary,
  rollbackUiDictionary,
  uiDictionaryHistory,
} from "@/lib/api";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";

export default function UiDictionaryEditor({ locale }) {
  const { t } = useI18n();
  const { showToast } = useToast();

  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const [preview, setPreview] = useState(null); // {errors}|{total,added,removed,unchanged}
  const [history, setHistory] = useState([]);
  const [isNew, setIsNew] = useState(false); // активного override ещё нет
  const [busy, setBusy] = useState(false);

  const loadHistory = useCallback(async () => {
    try {
      setHistory(await uiDictionaryHistory(locale));
    } catch {
      // молча
    }
  }, [locale]);

  const loadActive = useCallback(async () => {
    try {
      const active = await getUiDictionaryAdmin(locale);
      const hasData = active && active.data != null;
      setIsNew(!hasData);
      if (hasData) setText(JSON.stringify(active.data, null, 2));
      else setText("");
      setNote(active?.note || "");
    } catch (err) {
      showToast(err.message, { type: "error" });
    }
  }, [locale]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    loadActive();
    loadHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locale]);

  const parseJson = () => {
    try {
      const parsed = JSON.parse(text || "{}");
      if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("JSON должен быть объектом {ключ: значение}");
      }
      return parsed;
    } catch (err) {
      throw new Error(`Некорректный JSON: ${err.message}`);
    }
  };

  const onPreview = () => {
    let data;
    try {
      data = parseJson();
    } catch (err) {
      showToast(err.message, { type: "error" });
      return;
    }
    setBusy(true);
    importUiDictionary(locale, { data, note, confirm: false })
      .then((res) => setPreview(res))
      .catch((err) => showToast(err.message, { type: "error" }))
      .finally(() => setBusy(false));
  };

  const onApply = () => {
    let data;
    try {
      data = parseJson();
    } catch (err) {
      showToast(err.message, { type: "error" });
      return;
    }
    setBusy(true);
    importUiDictionary(locale, { data, note, confirm: true })
      .then(async (res) => {
        if (res.applied) {
          showToast(t("admin.uictl.applied"), { type: "success" });
          setPreview(null);
          await loadActive();
          await loadHistory();
        } else {
          setPreview(res);
        }
      })
      .catch((err) => showToast(err.message, { type: "error" }))
      .finally(() => setBusy(false));
  };

  const onRollback = (entry) => {
    if (!window.confirm(t("admin.uictl.rollbackConfirm", { version: entry.version }))) return;
    setBusy(true);
    rollbackUiDictionary(locale, entry.id)
      .then(async () => {
        showToast(t("admin.uictl.rolledBack"), { type: "success" });
        await loadActive();
        await loadHistory();
      })
      .catch((err) => showToast(err.message, { type: "error" }))
      .finally(() => setBusy(false));
  };

  const previewErrors = preview?.errors && preview.errors.length ? preview.errors : null;

  return (
    <div className="uictl-editor">
      {isNew ? (
        <p className="muted">{t("admin.uictl.newOverride")}</p>
      ) : (
        <p className="muted">{t("admin.uictl.editingCurrent")}</p>
      )}
      <p className="uictl-warning">{t("admin.uictl.replaceSemantics")}</p>
      <textarea
        className="uictl-textarea"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={10}
        placeholder={t("admin.uictl.placeholder")}
        spellCheck={false}
      />
      <input
        className="uictl-note"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder={t("admin.uictl.notePlaceholder")}
      />
      <div className="uictl-controls">
        <button className="modal-btn" disabled={busy} onClick={onPreview}>
          {t("admin.uictl.preview")}
        </button>
        <button className="modal-btn" disabled={busy} onClick={onApply}>
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
      {preview && !previewErrors && (
        <div className="stopwords-preview">
          <div>{t("admin.uictl.total", { count: preview.total })}</div>
          <div>{t("admin.uictl.added", { count: preview.added.length })}</div>
          <div>{t("admin.uictl.removed", { count: preview.removed.length })}</div>
          <div>{t("admin.uictl.unchanged", { count: preview.unchanged.length })}</div>
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
                  <button className="modal-btn" disabled={busy} onClick={() => onRollback(entry)}>
                    {t("admin.uictl.rollback")}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
