"use client";

import Link from "next/link";
import { CiteLink, documentHref, remarkCiteLinks, sourceHref } from "@/lib/chatSources";
import { inModelContext } from "@/lib/chatSourceContext.mjs";
import { groupSourcesByDocument } from "@/lib/chatAnswerState.mjs";
import { useI18n } from "@/i18n/LocaleContext";
import MarkdownViewer from "./MarkdownViewer";
import AppliedTerms from "./AppliedTerms";

export function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function SourceBadges({ sources, responseMode }) {
  const { t } = useI18n();
  if (!sources || sources.length === 0) return null;
  if (responseMode === "documents") {
    return (
      <div className="history-sources">
        {groupSourcesByDocument(sources).map((group) => (
          <div key={group.doc_id} className="history-document-group">
            {documentHref(group.source) ? (
              <Link href={documentHref(group.source)}>{group.filename}</Link>
            ) : group.filename}
            <ul>
              {group.sources.map((source, index) => (
                <li key={source.source_index ?? index}>
                  {sourceHref(source) ? <Link href={sourceHref(source)}>{source.title}</Link> : source.title}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className="history-sources">
      {sources.map((s, i) => {
        const href = sourceHref(s);
        const label = (
          <>
            {s.title || s.filename}
            {" "}<span className="meta">{t(inModelContext(s) ? "chat.sourceInContext" : "chat.sourceSearchOnly")}</span>
            {s.development_number && (
              <span className="source-dev-badge" title={s.development_name || t("chat.developmentTitle")}>
                {s.development_number}
              </span>
            )}
            {s.development_module && (
              <span className="source-dev-badge source-module-badge">{s.development_module}</span>
            )}
          </>
        );
        return href ? (
          <Link key={i} className="history-source history-source-link" href={href}>
            {label}
          </Link>
        ) : (
          <span key={i} className="history-source">
            {label}
          </span>
        );
      })}
    </div>
  );
}

export function HistoryMessage({ m, userLabel }) {
  const { t } = useI18n();
  const youLabel = userLabel ?? t("chat.you");
  const attempt = m.retrieval_metadata?.answer_attempt;
  const content =
    m.role === "assistant" ? (
      <MarkdownViewer
        className="okf-markdown chat-markdown"
        text={attempt && attempt.status !== "completed"
          ? t(attempt.status === "stopped" ? "chat.answerStopped" : "chat.answerIncomplete")
          : m.content}
        remarkPlugins={[remarkCiteLinks]}
        components={{
          a: (props) => <CiteLink sources={m.sources || []} {...props} />,
        }}
      />
    ) : (
      m.content
    );
  return (
    <div className={`msg ${m.role}`}>
      <div className="role">{m.role === "user" ? youLabel : t("chat.assistant")}</div>
      <div className="bubble history-bubble">{content}</div>
      {m.role === "assistant" && m.retrieval_metadata?.source_selection && (
        <div className="meta">{t("chat.selectedAnswerContext", { count: m.retrieval_metadata.source_selection.indexes.length })}</div>
      )}
      {m.role === "assistant" && m.retrieval_metadata?.search_limit_reached && (
        <div className="meta" role="status">
          {t(m.retrieval_metadata.search_depth >= 500 ? "chat.searchLimitReachedMax" : "chat.searchLimitReached", { depth: m.retrieval_metadata.search_depth })}
        </div>
      )}
      {m.role === "assistant" && m.retrieval_metadata && (
        <AppliedTerms status={m.retrieval_metadata.expansion_status} appliedTerms={m.retrieval_metadata.applied_terms} />
      )}
      <SourceBadges sources={m.sources} responseMode={attempt?.mode} />
    </div>
  );
}
