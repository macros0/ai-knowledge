"use client";

import { memo, useMemo } from "react";
import Link from "next/link";
import { documentHref, sourceHref } from "@/lib/chatSources";
import { inModelContext } from "@/lib/chatSourceContext.mjs";
import { groupSourcesByDocument } from "@/lib/chatAnswerState.mjs";
import { selectionState } from "@/lib/chatSourceSelection.mjs";
import { useI18n } from "@/i18n/LocaleContext";

function SelectionCheckbox({ state, label, onChange }) {
  return <input type="checkbox" className="source-select" aria-label={label}
    aria-checked={state === "some" ? "mixed" : state === "all"}
    checked={state === "all"} ref={(element) => { if (element) element.indeterminate = state === "some"; }}
    onChange={(event) => onChange(event.target.checked)} />;
}


const ChatSourceRow = memo(function ChatSourceRow({ source: s, selected, selectable, responseMode, onSelect }) {
  const { t } = useI18n();
  const href = sourceHref(s);
  const badge = s.point_type === "chunk" ? "\u{1F4E6}" : "\u{1F4C4}";
  return (
                      <li>
                        {selectable && (
                          <SelectionCheckbox state={selected ? "all" : "none"}
                            label={t("chat.selectFragment", { index: s.source_index, name: s.title })}
                            onChange={(checked) => onSelect([s.source_index], checked)} />
                        )}
                        <span className="source-badge">{badge}</span>{" "}
                        {href ? (
                          <>
                            <Link className="source-link" href={href}>
                              {s.title}
                            </Link>{" "}
                          </>
                        ) : (
                          s.title
                        )}
                        {t("chat.relevance", { pct: (s.score * 100).toFixed(0) })}
                        {" "}<span className="meta">{t(responseMode === "documents" ? "chat.sourceFound" : inModelContext(s) ? "chat.sourceInContext" : "chat.sourceSearchOnly")}</span>
                        {s.development_number && (
                          <span
                            className="source-dev-badge"
                            title={s.development_name || t("chat.developmentTitle")}
                          >
                            {s.development_number}
                          </span>
                        )}
                        {s.development_module && (
                          <span className="source-dev-badge source-module-badge">
                            {s.development_module}
                          </span>
                        )}
                        {s.snippet && <div className="source-snippet">{s.snippet}</div>}
                      </li>
  );
});

export const ChatSourceDocuments = memo(function ChatSourceDocuments({ sources, selectedIndexes, selectionEnabled, onSelect }) {
  const { t } = useI18n();
  const selectedSources = useMemo(() => new Set(selectedIndexes), [selectedIndexes]);
  const availableIndexes = useMemo(() => new Set(selectionEnabled
    ? sources.filter((source) => source.selectable === true && Number.isInteger(source.source_index)).map((source) => source.source_index)
    : []), [sources, selectionEnabled]);
  const groups = useMemo(() => groupSourcesByDocument(sources), [sources]);
  return <>
            {sources?.length > 0 && (
              <ul className="chat-document-list">
                {groups.map((group) => (
                  <li key={group.doc_id}>
                    {group.sources.some((source) => availableIndexes.has(source.source_index)) && <SelectionCheckbox state={selectionState(selectedSources, group.sources.filter((source) => availableIndexes.has(source.source_index)).map((source) => source.source_index))}
                      label={t("chat.selectDocument", { name: group.filename })}
                      onChange={(checked) => onSelect(group.sources.map((source) => source.source_index), checked)} />}
                    {documentHref(group.source) ? (
                      <Link href={documentHref(group.source)}>{group.filename}</Link>
                    ) : group.filename}
                    <span className="meta"> · {group.sources.length} {t("chat.fragments")}</span>
                  </li>
                ))}
              </ul>
            )}
  </>;
});

export default memo(function ChatSources({ sources, selectedIndexes, selectionEnabled, responseMode, open, onSelect, onToggle }) {
  const { t } = useI18n();
  const selectedSources = useMemo(() => new Set(selectedIndexes), [selectedIndexes]);
  const availableIndexes = useMemo(() => new Set(selectionEnabled
    ? sources.filter((source) => source.selectable === true && Number.isInteger(source.source_index)).map((source) => source.source_index)
    : []), [sources, selectionEnabled]);
  return <>
            {sources && sources.length > 0 && (
              <details
                className="sources"
                open={Boolean(open)}
                onToggle={(event) => {
                  // Controlled updates also emit toggle; only record a user change.
                  if (event.currentTarget.open !== Boolean(open)) onToggle(event.currentTarget.open);
                }}
              >
                <summary>{t("chat.sources")}</summary>
                {open && <ol style={{ "--source-number-digits": String(sources.length).length }}>
                  {sources.map((source, index) => <ChatSourceRow
                    key={source.source_index ?? JSON.stringify([source.doc_id, source.source_slug ?? source.filepath, source.chunk_index, source.point_type, index])}
                    source={source} selected={selectedSources.has(source.source_index)}
                    selectable={availableIndexes.has(source.source_index)} responseMode={responseMode} onSelect={onSelect} />)}
                </ol>}
              </details>
            )}
  </>;
});
