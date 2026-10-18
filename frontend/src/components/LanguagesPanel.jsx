"use client";

import { apiToast } from "@/lib/apiToast.mjs";

import { useCallback, useEffect, useState } from "react";
import {
  activateLocale,
  createLocale,
  disableLocale,
  friendlyApiError,
  listLocales,
} from "@/lib/api";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";
import { canActivate, canDisable } from "@/lib/localeActions.mjs";
import UiDictionaryEditor from "./UiDictionaryEditor";
import BackfillControl from "./BackfillControl";

import StopwordsEditor from "./StopwordsEditor";

function StatusBadge({ status }) {
  const { t } = useI18n();
  return (
    <span className={`job-status ${status}`}>
      {t(`admin.languages.status.${status}`)}
    </span>
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
  const [toolDirty, setToolDirty] = useState(false);
  const [toolBusy, setToolBusy] = useState(false);

  const toggleTool = (loc, tool) => {
    if (toolBusy || (toolDirty && !window.confirm(t("admin.glossary.unsavedChangesConfirm")))) return;
    setToolDirty(false);
    setActiveTool(activeTool && activeTool.code === loc && activeTool.tool === tool ? null : {code: loc, tool});
  };

  const load = useCallback(async () => {
    try {
      setLocales(await listLocales());
    } catch (err) {
      showToast({ ...apiToast(err, t, { type: "error" }), message: t("admin.languages.loadError", { message: friendlyApiError(err, t) }) });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const notifyLocalesChanged = () => {
    // Тоггл языка в топбаре живёт в layout и не перемонтируется — шлём событие,
    // чтобы он перечитал активные языки сразу (без полного reload).
    try {
      window.dispatchEvent(new Event("okf:locales-changed"));
    } catch {
      // SSR/не-window — не критично
    }
  };

  const onCreate = async () => {
    if (!code.trim() || !name.trim()) return;
    try {
      await createLocale(code.trim(), name.trim());
      showToast(t("admin.languages.created", { code: code.trim() }));
      setCode("");
      setName("");
      await load();
      notifyLocalesChanged();
    } catch (err) {
      showToast({ ...apiToast(err, t, {
        type: "error",
      }), message: t("admin.languages.createError", { message: friendlyApiError(err, t) }) });
    }
  };

  const onActivate = async (c) => {
    try {
      if (toolBusy) return;
      await activateLocale(c, {expected_reference_revision: locales.find(loc => loc.code === c)?.reference_revision});
      showToast(t("admin.languages.activated"));
      await load();
      notifyLocalesChanged();
    } catch (err) {
      if (err.code === "version_conflict") await load();
      showToast({ ...apiToast(err, t, { type: "error" }), message: t("admin.languages.actError", { message: friendlyApiError(err, t) }) });
    }
  };

  const onDisable = async (c) => {
    try {
      if (toolBusy) return;
      await disableLocale(c, {expected_reference_revision: locales.find(loc => loc.code === c)?.reference_revision});
      showToast(t("admin.languages.disabled"));
      await load();
      notifyLocalesChanged();
    } catch (err) {
      if (err.code === "version_conflict") await load();
      showToast({ ...apiToast(err, t, { type: "error" }), message: t("admin.languages.actError", { message: friendlyApiError(err, t) }) });
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
                  onClick={() => toggleTool(loc.code, "stopwords")}
                  title={t("admin.languages.editStopwords")}
                >
                  {t("admin.languages.editStopwords")}
                </button>
                <button
                  className="modal-btn"
                  onClick={() => toggleTool(loc.code, "uictl")}
                  title={t("admin.languages.uictl")}
                >
                  {t("admin.languages.uictl")}
                </button>
                <button
                  className="modal-btn"
                  onClick={() => toggleTool(loc.code, "backfill")}
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
              <StopwordsEditor key={activeTool.code} locale={activeTool.code} onDirtyChange={setToolDirty} onBusyChange={setToolBusy} />
            </>
          )}
          {activeTool.tool === "uictl" && (
            <>
              <h3>{t("admin.uictl.title", { code: activeTool.code })}</h3>
              <UiDictionaryEditor key={activeTool.code} locale={activeTool.code} onDirtyChange={setToolDirty} onBusyChange={setToolBusy} />
            </>
          )}
          {activeTool.tool === "backfill" && (
            <>
              <h3>{t("admin.backfill.title", { code: activeTool.code })}</h3>
              <BackfillControl key={activeTool.code} locale={activeTool.code} onBusyChange={setToolBusy} />
            </>
          )}
        </div>
      )}
    </section>
  );
}
