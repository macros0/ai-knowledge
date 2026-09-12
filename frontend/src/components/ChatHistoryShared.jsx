"use client";

import Link from "next/link";
import { CiteLink, remarkCiteLinks, sourceHref } from "@/lib/chatSources";
import { useI18n } from "@/i18n/LocaleContext";
import MarkdownViewer from "./MarkdownViewer";
import AppliedTerms from "./AppliedTerms";

export function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function SourceBadges({ sources }) {
  const { t } = useI18n();
  if (!sources || sources.length === 0) return null;
  return (
    <div className="history-sources">
      {sources.map((s, i) => {
        const href = sourceHref(s);
        const label = (
          <>
            {s.title || s.filename}
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
  const content =
    m.role === "assistant" ? (
      <MarkdownViewer
        className="okf-markdown chat-markdown"
        text={m.content}
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
      {m.role === "assistant" && m.retrieval_metadata && (
        <AppliedTerms status={m.retrieval_metadata.expansion_status} appliedTerms={m.retrieval_metadata.applied_terms} />
      )}
      <SourceBadges sources={m.sources} />
    </div>
  );
}
