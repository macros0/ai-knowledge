"use client";

import { appliedTermsSummary } from "@/lib/glossaryUi.mjs";
import { useI18n } from "@/i18n/LocaleContext";
import ChatDisclosureSummary from "./ChatDisclosureSummary";

export default function AppliedTerms({ status = "disabled", appliedTerms = [] }) {
  const { t } = useI18n();
  const items = appliedTermsSummary(appliedTerms);
  if (!items.length && status !== "unavailable") return null;
  return (
    <div className={`applied-terms${status === "unavailable" ? " unavailable" : ""}`}>
      <strong>{t("chat.glossary.title")}</strong>
      {status === "unavailable" ? (
        <span>{t("chat.glossary.unavailable")}</span>
      ) : (
        <details className="chat-disclosure">
          <ChatDisclosureSummary showLabel={t("chat.glossary.showForms")} hideLabel={t("chat.glossary.collapseForms")} meta={t("chat.glossary.count", { count: items.length })} />
          <div className="chat-disclosure-content">
          <ul>
            {items.map((item) => (
              <li key={`${item.canonical}-${item.matched.join("|")}`}>
                <span>{item.matched.join(", ")} → <b>{item.displayName}</b></span>
                {item.systemRule === "sap_infotype" && <small>{t("chat.glossary.infotypeRule")}</small>}
                {item.added.length > 0 && <small>{item.added.join(", ")}</small>}
              </li>
            ))}
          </ul>
          </div>
        </details>
      )}
      {status === "limited" && <span className="applied-terms-note">{t("chat.glossary.limited")}</span>}
    </div>
  );
}
