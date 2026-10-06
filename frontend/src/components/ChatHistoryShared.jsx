"use client";

import {useState} from "react";
import {useChat} from "@/context/ChatContext";
import ChatSources from "./ChatSources";
import { CiteLink, remarkCiteLinks } from "@/lib/chatSources";
import { searchLimitWarning } from "@/lib/chatAnswerState.mjs";
import { useI18n } from "@/i18n/LocaleContext";
import MarkdownViewer from "./MarkdownViewer";
import AppliedTerms from "./AppliedTerms";

export function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function SourceBadges({sources,responseMode}) {
  const [open,setOpen]=useState(false);
  const {sourceView,setSourceView}=useChat();
  return <ChatSources sources={sources || []} responseMode={responseMode} open={open} onToggle={setOpen} view={sourceView} onViewChange={setSourceView}/>;
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
      {m.role === "assistant" && m.retrieval_metadata?.search_doc_ids != null && (
        <div className="meta">{t("chat.scopeUsed", { count: m.retrieval_metadata.search_doc_ids.length })}</div>
      )}
      {m.role === "assistant" && searchLimitWarning(m.retrieval_metadata?.search_limit_reached, m.sources?.length ?? 0, m.retrieval_metadata?.search_depth) && (
        <div className="meta" role="status">
          {t(searchLimitWarning(m.retrieval_metadata.search_limit_reached, m.sources?.length ?? 0, m.retrieval_metadata.search_depth), { depth: m.retrieval_metadata.search_depth })}
        </div>
      )}
      {m.role === "assistant" && m.retrieval_metadata && (
        <AppliedTerms status={m.retrieval_metadata.expansion_status} appliedTerms={m.retrieval_metadata.applied_terms} />
      )}
      <SourceBadges sources={m.sources} responseMode={attempt?.mode} />
    </div>
  );
}
