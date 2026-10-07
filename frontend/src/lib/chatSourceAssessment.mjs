import {retryRequestOptions} from "./chatComposer.mjs";

const PREFERENCE_KEY = "okf.chat.assessSources";
export function readAssessmentPreference(storage, fallback) {
  try {
    const value = storage?.getItem(PREFERENCE_KEY);
    if (value === "true") return true;
    if (value === "false") return false;
  } catch { /* Storage is optional. */ }
  return fallback;
}
export function writeAssessmentPreference(storage, enabled) {
  try { storage?.setItem(PREFERENCE_KEY, String(Boolean(enabled))); } catch { /* Storage is optional. */ }
}
export function assessmentView(outcome) {
  if (outcome?.status === "unavailable") return "chat.assessment.unavailable";
  if (outcome?.status === "running") return "chat.assessment.running";
  if (outcome?.status !== "completed") return null;
  return ["allow", "reject", "uncertain"].includes(outcome.decision) ? `chat.assessment.${outcome.decision}` : null;
}
export function applyAssessmentOutcome(message, outcome) {
  if (!outcome) return message;
  return {...message, sourceAssessment: outcome,
    sourcesOpen: message.sourcesTouched ? message.sourcesOpen : outcome.decision === "reject" ? false : message.sourcesOpen};
}
export function withoutAssessmentOptions(message) {
  return {...retryRequestOptions(message), requestAssessSources: false};
}
export function historyAssessmentMessage(message, sessionId) {
  const metadata = message.retrieval_metadata || {};
  const req = metadata.chat_request || {};
  return {...message, text: message.content, query: metadata.answer_attempt?.query ?? req.query,
    sourceAssessment: metadata.source_assessment, attemptId: metadata.answer_attempt?.id,
    requestSessionId: sessionId, requestAssessSources: metadata.source_assessment_replay?.requested_enabled ?? undefined,
    requestUseGlossary: req.use_glossary, requestMailMode: req.mail_mode ?? metadata.mail_mode,
    requestTags: req.tags, requestTopK: req.top_k, requestMode: req.mode,
    requestSearchDepth: req.search_depth ?? metadata.search_depth,
    requestSourceSelection: req.source_selection, requestDocIds: req.search_doc_ids,
    requestSourceLocales: req.source_locales, requestIncludeUnknownLocale: req.include_unknown_source_locale,
    requestSourceLocale: req.include_unknown_source_locale ? "unknown" : req.source_locales?.[0] ?? "",
    requestLocale: req.locale, requestDense: req.dense, requestBm25: req.bm25,
    responseMode: req.response_mode ?? metadata.response_mode ?? metadata.answer_attempt?.mode};
}

export function editAssessmentOptions(message) {
  const {requestSourceSelection: _selection, ...options} = retryRequestOptions(message);
  return options;
}
export function selectedAssessmentOptions(message) {
  return {...retryRequestOptions(message), responseMode: "full", requestAssessSources: false, requestSourceSelection: {
    attempt_id: message.attemptId, indexes: [...(message.selectedSourceIndexes ?? [])],
  }};
}
