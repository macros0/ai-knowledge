"use client";

import { memo, useCallback, useMemo } from "react";
import Link from "next/link";
import { searchLimitWarning } from "@/lib/chatAnswerState.mjs";
import { selectableSources } from "@/lib/chatSourceSelection.mjs";
import { useI18n } from "@/i18n/LocaleContext";
import { CheckIcon, CopyIcon } from "./icons";
import ErrorReference from "./ErrorReference";
import AppliedTerms from "./AppliedTerms";
import ChatAnswer from "./ChatAnswer";
import ChatSources from "./ChatSources";

const EMPTY = [];

export default memo(function ChatMessageView({ message: m, index, isLatest = true, isPending, actionsPending, isCopied, searchDepthMax,
  onEdit, onRefreshSearch, onCopy, onSelect, onAnswerSelected, onAddToScope, onRetry, onRepeatWithoutGlossary, onStop, onToggleSources, onToggleSourceGroup }) {
  const { t } = useI18n();
  const sources = m.sources ?? EMPTY;
  const selectedIndexes = m.selectedSourceIndexes ?? EMPTY;
  const availableSources = useMemo(() => selectableSources({ sources, attemptId: m.attemptId, responseMode: m.responseMode }),
    [sources, m.attemptId, m.responseMode]);
  const selectionEnabled = Boolean(m.attemptId && ["documents", "fast", "full"].includes(m.responseMode));
  const sourcesOpen = Boolean(m.sourcesOpen) && (m.sourcesTouched || isLatest);
  const select = useCallback((indexes, checked) => onSelect(index, indexes, checked), [index, onSelect]);
  const toggle = useCallback((open) => onToggleSources(index, open), [index, onToggleSources]);
  return (
          <div className={`msg ${m.role}`} data-request-index={m.role === "user" ? index : undefined}>
            <div className="role-row">
              <div className="role">{m.role === "user" ? t("chat.you") : t("chat.assistant")}</div>
              {m.role === "assistant" && (
                <button
                  type="button"
                  className="copy-btn"
                  disabled={isPending}
                  onClick={() => onCopy(index, m.text)}
                  title={t("chat.copyAnswer")}
                >
                  {isCopied ? <CheckIcon size={14} /> : <CopyIcon size={14} />}
                  {isCopied ? t("chat.copied") : t("chat.copy")}
                </button>
              )}
            </div>
            <div className="bubble">
              {m.role === "assistant" ? (
                <ChatAnswer text={m.text} sources={sources} />
              ) : (
                m.text
              )}
            </div>
            {m.role === "assistant" && availableSources.length > 0 && selectedIndexes.length > 0 && (
              <div className="source-selection-actions">
                <button type="button" className="btn ghost" onClick={() => select( m.sources.map((source) => source.source_index), false)} disabled={!m.selectedSourceIndexes?.length}>{t("chat.clearSourceSelection")}</button>
                <button type="button" className="btn" disabled={actionsPending || !m.selectedSourceIndexes?.length} onClick={() => onAnswerSelected(m)}>
                  {t("chat.answerSelected", { count: m.selectedSourceIndexes?.length ?? 0 })}
                </button>
                <button type="button" className="btn ghost" disabled={!m.selectedSourceIndexes?.length} onClick={() => onAddToScope(m)}>{t("chat.scopeAddSelected")}</button>
                <span className="meta">{t("chat.selectionDescription")}</span>
              </div>
            )}
            {m.role === "assistant" && m.requestSourceSelection && (
              <div className="meta">{t("chat.selectedAnswerContext", { count: m.requestSourceSelection.indexes.length })}</div>
            )}
            {m.role === "assistant" && m.requestDocIds != null && (
              <div className="meta">{t("chat.scopeUsed", { count: m.requestDocIds.length })}</div>
            )}
            {m.role === "assistant" && searchLimitWarning(m.searchLimitReached, m.sources?.length ?? 0, m.requestSearchDepth, searchDepthMax) && (
              <div className="meta" role="status">
                {t(searchLimitWarning(m.searchLimitReached, m.sources?.length ?? 0, m.requestSearchDepth, searchDepthMax), { depth: m.requestSearchDepth })}
              </div>
            )}
            {m.role === "assistant" && m.responseMode === "fast" && m.sources?.length > 0 && (
              <div className="meta" role="status">
                {t("chat.fastCoverage", {
                  used: m.sources.filter((source) => source.in_model_context).length,
                  found: m.sources.length,
                })}
              </div>
            )}
            {m.role === "assistant" && m.progress?.phase && isPending && (
              <div className="chat-progress" role="status">
                {m.progress.phase === "synthesis"
                  ? t("chat.synthesizing")
                  : t("chat.batchProgress", { done: m.progress.batches_done ?? 0, total: m.progress.batches_total ?? 0 })}
              </div>
            )}
            {m.role === "assistant" && isPending && (
              <button type="button" className="btn ghost" onClick={onStop}>{t("chat.stopAnswer")}</button>
            )}
            {m.role === "assistant" && (m.stopped || (m.failed && m.retryable)) && (
              <button type="button" className="btn ghost" disabled={actionsPending} onClick={() => onRetry(m)}>{t("chat.restartAnswer")}</button>
            )}
            {m.role === "assistant" && m.selectionUnavailable && <button type="button" className="btn ghost" disabled={actionsPending} onClick={()=>onRefreshSearch(m)}>{t("ux.searchAgain")}</button>}
            {m.role === "assistant" && (m.failed || m.stopped) && <button type="button" className="btn ghost" disabled={actionsPending} onClick={()=>onEdit(m)}>{t("ux.editQuestion")}</button>}
            {m.role === "assistant" && <ErrorReference requestId={m.requestId} localReportId={m.localReportId} />}
            {m.role === "assistant" && <AppliedTerms status={m.expansion_status} appliedTerms={m.applied_terms} />}
            {m.role === "assistant" && m.applied_terms?.length > 0 && (
              <button type="button" className="btn ghost glossary-repeat" onClick={() => onRepeatWithoutGlossary(m)} disabled={actionsPending}>
                {t("chat.glossary.repeatWithout")}
              </button>
            )}
            <ChatSources sources={sources} selectedIndexes={selectedIndexes} selectionEnabled={selectionEnabled}
              responseMode={m.responseMode} open={sourcesOpen} onSelect={select} onToggle={toggle}
              expandedGroups={m.sourceGroupsOpen} onToggleGroup={onToggleSourceGroup ? (id,expanded)=>onToggleSourceGroup(index,id,expanded) : undefined} />
            {m.role === "assistant" && m.uploadHint && (!m.sources || m.sources.length === 0) && (
              <div className="chat-upload-hint">
                {t("chat.noSources")}{" "}
                <Link className="chat-upload-hint-link" href={m.uploadHint.href}>
                  {m.uploadHint.label}
                </Link>
              </div>
            )}
          </div>
  );
});
