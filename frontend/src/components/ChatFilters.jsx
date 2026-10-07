"use client";
import {useI18n} from "@/i18n/LocaleContext";
import ChatDisclosureSummary from "./ChatDisclosureSummary";
export default function ChatFilters({summary,onClear,children}) {
  const {t}=useI18n();
  return <details className="chat-filters chat-disclosure">
    <ChatDisclosureSummary showLabel={t("chat.showSearchFilters")} hideLabel={t("chat.collapseSearchFilters")} />
    <div className="chat-filter-controls chat-disclosure-content">{children}<button type="button" className="btn ghost" onClick={onClear} disabled={!summary.some(x=>x.removable)}>{t("docs.resetFilters")}</button></div>
  </details>;
}
export function ChatFilterSummary({summary,onRemove}) {
  const {t}=useI18n();
  return <div className="chat-filter-summary" aria-label={t("ux.searchFilters")}>{summary.map(item=>item.removable ?
    <button type="button" className="filter-chip" key={item.id} onClick={()=>onRemove(item.id)} aria-label={t("ux.removeFilter",{name:t(item.key,item.params)})}>{t(item.key,item.params)} <span aria-hidden="true">×</span></button> :
    <span className="meta" key={item.id}>{t(item.key,item.params)}</span>)}</div>;
}
