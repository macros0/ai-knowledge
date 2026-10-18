"use client";

import { useI18n } from "@/i18n/LocaleContext";

export default function EditorLeaseNotice({ state, ownerName, remainingSeconds, expiryAction = "retain_draft",
  onContinue, onRetry, onCopy }) {
  const { t } = useI18n();
  if (state === "viewing") return null;
  const warning = state === "editing" && remainingSeconds > 0;
  return <div className={`reference-edit-notice ${warning ? "warning" : ""}`} role="status" aria-live="polite">
    {state === "acquiring" && <p>{t("reference.acquiring")}</p>}
    {state === "blocked" && <p>{t("reference.blocked", { name: ownerName || t("reference.anotherEditor") })}</p>}
    {state === "editing" && !warning && <p>{t("reference.idleHint")}</p>}
    {warning && <>
      <p>{t(expiryAction === "close" ? "reference.expiresClose" : "reference.expiresRetain")}</p>
      <p aria-live="off">{t("reference.countdown", { seconds: remainingSeconds })}</p>
      <button type="button" className="btn" onClick={onContinue}>{t("reference.continue")}</button>
    </>}
    {state === "lost" && <p>{t(expiryAction === "close" ? "reference.lostClose" : "reference.lostRetain")}</p>}
    {state === "available" && <p>{t("reference.available")}</p>}
    {["available", "blocked", "lost"].includes(state) && <button type="button" className="btn" onClick={onRetry}>{t("reference.retry")}</button>}
    {state === "lost" && onCopy && <button type="button" className="btn ghost" onClick={onCopy}>{t("reference.copyDraft")}</button>}
    {state === "applying" && <p>{t("reference.applying")}</p>}
  </div>;
}
