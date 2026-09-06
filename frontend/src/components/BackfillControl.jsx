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
  translationPending,
} from "@/lib/api";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";

const ENTITY_LABEL_KEYS = {
  tags: "admin.backfill.entTags",
  developments: "admin.backfill.entDevs",
  attributes: "admin.backfill.entAttrs",
};
const ALL_ENTITIES = Object.keys(ENTITY_LABEL_KEYS);

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

  const selectedEntities = ALL_ENTITIES.filter((e) => checked.has(e));
  const providerOff = provider === "off";

  const loadPending = useCallback(async () => {
    if (!code || !selectedEntities.length) {
      setPending({});
      return;
    }
    try {
      setPending(await translationPending(code, selectedEntities));
    } catch {
      // молча — счётчик информативный
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code, checked]);

  useEffect(() => {
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

  const run = () => {
    setBusy(true);
    setResult(null);
    backfillTranslations(code, selectedEntities)
      .then((res) => setResult(res.result || {}))
      .catch((err) => showToast(friendlyApiError(err), { type: "error" }))
      .finally(() => setBusy(false));
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
        disabled={busy || providerOff || selectedEntities.length === 0 || pendingTotal === 0}
        onClick={run}
        title={
          providerOff
            ? t("admin.backfill.providerOff")
            : selectedEntities.length === 0 || pendingTotal === 0
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
