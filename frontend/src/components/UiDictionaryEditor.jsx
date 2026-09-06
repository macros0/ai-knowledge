"use client";

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
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";

export default function UiDictionaryEditor({ locale }) {
  const { t } = useI18n();
  const { showToast } = useToast();

  // Guard от передачи объекта вместо строки (инцидент «[object Object]», P0).
  const code = typeof locale === "string" && locale.trim() ? locale.trim() : null;

  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const [result, setResult] = useState(null); // ответ import: {errors, applied, preview:{total,added,...}}
  const [history, setHistory] = useState([]);
  const [isNew, setIsNew] = useState(false); // активного override ещё нет
  const [loadError, setLoadError] = useState(null); // текст ошибки загрузки (или null)
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  // Флаг «пользователь уже правил JSON» — реф, чтобы не перезаписывать при retry.
  const dirtyRef = useRef(false);
  const textRef = useRef("");

  const onTextChange = (v) => {
    textRef.current = v;
    dirtyRef.current = true;
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
      if (!dirtyRef.current) {
        setText(hasData ? JSON.stringify(active.data, null, 2) : "");
        textRef.current = hasData ? JSON.stringify(active.data, null, 2) : "";
        setNote(active?.note || "");
      } else {
        // Правки пользователя сохранены; метаданные сервера всё равно обновляем.
        setNote(active?.note || "");
      }
      return true;
    } catch (err) {
      setLoadError(friendlyApiError(err));
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

  const parseJson = () => {
    try {
      const parsed = JSON.parse(textRef.current || "{}");
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
      showToast(t("admin.uictl.invalidJson", { error: err.message }), { type: "error" });
      return;
    }
    setBusy(true);
    importUiDictionary(code, { data, note, confirm: false })
      .then((res) => setResult(res))
      .catch((err) => showToast(friendlyApiError(err), { type: "error" }))
      .finally(() => setBusy(false));
  };

  const onApply = () => {
    let data;
    try {
      data = parseJson();
    } catch (err) {
      showToast(t("admin.uictl.invalidJson", { error: err.message }), { type: "error" });
      return;
    }
    setBusy(true);
    importUiDictionary(code, { data, note, confirm: true })
      .then(async (res) => {
        if (res.applied) {
          showToast(t("admin.uictl.applied"), { type: "success" });
          setResult(null);
          dirtyRef.current = false; // применено — сервер теперь источник
          await loadActive();
          await loadHistory();
        } else {
          setResult(res);
        }
      })
      .catch((err) => showToast(friendlyApiError(err), { type: "error" }))
      .finally(() => setBusy(false));
  };

  const onRollback = (entry) => {
    if (!window.confirm(t("admin.uictl.rollbackConfirm", { version: entry.version }))) return;
    setBusy(true);
    rollbackUiDictionary(code, entry.id)
      .then(async () => {
        showToast(t("admin.uictl.rolledBack"), { type: "success" });
        dirtyRef.current = false;
        await loadActive();
        await loadHistory();
      })
      .catch((err) => showToast(friendlyApiError(err), { type: "error" }))
      .finally(() => setBusy(false));
  };

  const previewErrors = result?.errors && result.errors.length ? result.errors : null;

  if (!code) {
    return <div className="uictl-errors">{t("admin.languages.invalidLocale")}</div>;
  }

  return (
    <div className="uictl-editor">
      {isNew ? (
        <p className="muted">{t("admin.uictl.newOverride")}</p>
      ) : (
        <p className="muted">{t("admin.uictl.editingCurrent")}</p>
      )}
      <p className="uictl-warning">{t("admin.uictl.replaceSemantics")}</p>

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
        onChange={(e) => onTextChange(e.target.value)}
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
