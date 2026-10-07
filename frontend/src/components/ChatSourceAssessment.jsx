"use client";
import {useI18n} from "@/i18n/LocaleContext";
import {assessmentView} from "@/lib/chatSourceAssessment.mjs";

export default function ChatSourceAssessment({outcome, candidateCount = 0, responseMode, pending,
  onShowCandidates, onContinueWithoutAssessment}) {
  const {t} = useI18n();
  let key = assessmentView(outcome);
  if (!key) return null;
  if (outcome.status === "unavailable" && responseMode === "documents") key = "chat.assessment.unavailableDocuments";
  const rejected = outcome.decision === "reject";
  return <div className="chat-source-assessment">
    <div className="meta" role="status" aria-live="polite">{t(key, {count: outcome.sampled_count ?? 0})}</div>
    {rejected && <div className="source-selection-actions">
      {candidateCount > 0 && onShowCandidates && <button type="button" className="btn ghost" onClick={onShowCandidates}>{t("chat.assessment.showCandidates")}</button>}
      {onContinueWithoutAssessment && <button type="button" className="btn ghost" disabled={pending} onClick={onContinueWithoutAssessment}>{t("chat.assessment.continueWithout")}</button>}
    </div>}
  </div>;
}
