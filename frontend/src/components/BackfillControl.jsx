"use client";

// Запуск перевода справочников (теги/разработки/атрибуты) для локали — Этап 7.
// Перед запуском показывает: provider + model (фактический маршрут), число объектов
// без ручного перевода (count_pending — тот же источник, что и бэкфилл) и
// предупреждение про машинные черновики. При translation_provider=off кнопка
// запуска недоступна (иначе бэкфилл «ничего не сделал бы» без явной причины).

import { useCallback, useEffect, useState } from "react";
import {
  backfillTranslations,
  friendlyApiError,
  getChatSettings,
  backfillGlossaryTranslations,
  listGlossary,
  pendingGlossaryTranslations,
  translationPending,
} from "@/lib/api";
import { chunkTermIds } from "@/lib/glossaryUi.mjs";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";

const ENTITY_LABEL_KEYS = {
  tags: "admin.backfill.entTags",
  developments: "admin.backfill.entDevs",
  attributes: "admin.backfill.entAttrs",
  glossary: "admin.backfill.entGlossary",
};
const ALL_ENTITIES = Object.keys(ENTITY_LABEL_KEYS);
const LEGACY_ENTITIES = ALL_ENTITIES.filter((entity) => entity !== "glossary");

export default function BackfillControl({ locale }) {
  const { t } = useI18n();
  const { showToast } = useToast();

  // Guard от передачи объекта вместо строки (инцидент «[object Object]», P0).
  const code = typeof locale === "string" && locale.trim() ? locale.trim() : null;

  const [provider, setProvider] = useState(null); // "llm" | "off"
  const [model, setModel] = useState("");
  const [checked, setChecked] = useState(
    () => new Set(ALL_ENTITIES) // [Set, ...]
  );
  const [pending, setPending] = useState({});
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getChatSettings()
      .then((s) => {
        setProvider(s.translation_provider || "llm");
        setModel(s.translation_model || "");
      })
      .catch(() => {});
  }, []);

  const selectedEntities = LEGACY_ENTITIES.filter((e) => checked.has(e));
  const providerOff = provider === "off";

  const loadPending = useCallback(async () => {
    if (!code || !selectedEntities.length) {
      setPending({});
      return;
    }
    try {
      const [legacy, glossary] = await Promise.all([
        selectedEntities.length ? translationPending(code, selectedEntities) : Promise.resolve({}),
        checked.has("glossary") ? pendingGlossaryTranslations(code) : Promise.resolve({ pending: {} }),
      ]);
      setPending({ ...legacy, glossary: glossary.pending?.eligible_for_backfill || 0 });
    } catch {
      // молча — счётчик информативный
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code, checked]);

  useEffect(() => {
    // Initial load synchronizes server state into this admin control.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadPending();
  }, [loadPending]);

  const toggle = (ent) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(ent)) next.delete(ent);
      else next.add(ent);
      return next;
    });
  };

  const run = async () => {
    setBusy(true);
    setResult(null);
    try {
      const result = {};
      if (selectedEntities.length) {
        const legacy = await backfillTranslations(code, selectedEntities);
        Object.assign(result, legacy.result || {});
      }
      if (checked.has("glossary")) {
        const page = await listGlossary({ enabled: true, limit: 100, offset: 0 });
        const glossaryIds = (page.terms || [])
          .filter((term) => term.canonical_locale !== code)
          .filter((term) => !term.translations?.some((item) => item.locale === code && !item.is_machine_translated))
          .map((term) => term.id);
        const glossaryResult = {};
        for (const batch of chunkTermIds(glossaryIds)) {
          const response = await backfillGlossaryTranslations(code, batch);
          for (const [key, value] of Object.entries(response || {})) {
            if (typeof value === "number") glossaryResult[key] = (glossaryResult[key] || 0) + value;
          }
        }
        result.glossary = glossaryResult;
      }
      setResult(result);
      await loadPending();
    } catch (err) {
      showToast(friendlyApiError(err, t), { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  const pendingTotal = ALL_ENTITIES.reduce(
    (acc, e) => acc + (pending[e] || 0),
    0
  );

  if (!code) {
    return <div className="uictl-errors">{t("admin.languages.invalidLocale")}</div>;
  }

  return (
    <div className="backfill-control">
      <p className="backfill-meta">
        {t("admin.backfill.provider")}: <strong>{provider ?? "…"}</strong>
        {provider === "llm" && model ? (
          <>
            {" · "}
            {t("admin.backfill.model")}: <strong>{model}</strong>
          </>
        ) : null}
        {" · "}
        {t("admin.backfill.locale", { locale })}
      </p>

      <div className="backfill-entities">
        {ALL_ENTITIES.map((ent) => (
          <label key={ent} className="backfill-check">
            <input
              type="checkbox"
              checked={checked.has(ent)}
              onChange={() => toggle(ent)}
            />
            {t(ENTITY_LABEL_KEYS[ent])}
            {pending[ent] != null ? ` (${pending[ent]})` : ""}
          </label>
        ))}
      </div>

      <p className="muted">
        {t("admin.backfill.pendingTotal", { count: pendingTotal })}
      </p>
      <p className="uictl-warning">{t("admin.backfill.warning")}</p>

      <button
        className="modal-btn"
        disabled={busy || providerOff || (selectedEntities.length === 0 && !checked.has("glossary")) || pendingTotal === 0}
        onClick={run}
        title={
          providerOff
            ? t("admin.backfill.providerOff")
            : (selectedEntities.length === 0 && !checked.has("glossary")) || pendingTotal === 0
              ? t("admin.backfill.nothing")
              : undefined
        }
      >
        {busy ? t("admin.backfill.running") : t("admin.backfill.run")}
      </button>
      {providerOff && (
        <p className="muted backfill-provider-off">{t("admin.backfill.providerOff")}</p>
      )}

      {result && (
        <div className="backfill-result">
          {ALL_ENTITIES.filter((e) => result[e]).map((e) => (
            <div key={e}>
              {t(ENTITY_LABEL_KEYS[e])}: created={result[e].created ?? 0} failed={result[e].failed ?? 0}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
