"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { chat, cancelChatAttempt, friendlyApiError, getSourceLocaleFacets, listAttributeValues, listDevelopments } from "@/lib/api";
import { CiteLink, documentHref, remarkCiteLinks, sourceHref } from "@/lib/chatSources";
import { inModelContext } from "@/lib/chatSourceContext.mjs";
import { applyAnswerEvent, groupSourcesByDocument } from "@/lib/chatAnswerState.mjs";
import { updateSelection, selectionState, selectableSources } from "@/lib/chatSourceSelection.mjs";
import { facetOptions } from "@/lib/sourceLocales.mjs";
import { addScopeDocuments, scopeRequestIds, SEARCH_SCOPE_MAX_DOCUMENTS } from "@/lib/chatSearchScope.mjs";
import TagPicker from "./TagPicker";
import DevelopmentFilter from "./DevelopmentFilter";
import ModulePicker from "./ModulePicker";
import MarkdownViewer from "./MarkdownViewer";
import { CheckIcon, CopyIcon } from "./icons";
import { retryMailMode } from "@/lib/chatMailFilter.mjs";
import { useChat } from "@/context/ChatContext";
import { useAuth } from "@/context/AuthContext";
import { useI18n } from "@/i18n/LocaleContext";
import AppliedTerms from "./AppliedTerms";
import SearchableSelect from "./SearchableSelect";
import ChatSearchScope from "./ChatSearchScope";
import ChatRequestNavigation from "./ChatRequestNavigation";

function getPresetLabel(preset, settings, t) {
  if (preset === settings.top_k_default) return t("chat.topkStandard");
  const minPreset = Math.min(...settings.top_k_presets);
  const maxPreset = Math.max(...settings.top_k_presets);
  if (preset === minPreset) return t("chat.topkShort");
  if (preset === maxPreset) return t("chat.topkDetailed");
  return String(preset);
}

function SelectionCheckbox({ state, label, onChange }) {
  return <input type="checkbox" className="source-select" aria-label={label}
    aria-checked={state === "some" ? "mixed" : state === "all"}
    checked={state === "all"} ref={(element) => { if (element) element.indeterminate = state === "some"; }}
    onChange={(event) => onChange(event.target.checked)} />;
}

export default function ChatPanel() {
  const { messages, tags, pending, settings, selectedMode, searchDepth, setSearchDepth, sessionId, mailMode, setMailMode, useGlossary, setUseGlossary, setSessionId, startNewChat, setMessages, setTags, setPending, setSelectedMode, MODE_LABELS, searchScopeDocuments, setSearchScopeDocuments, searchScopeEnabled, setSearchScopeEnabled } = useChat();
  const { user } = useAuth();
  const { t, locale } = useI18n();
  const [query, setQuery] = useState("");
  const [responseMode, setResponseMode] = useState("fast");
  const [selectedTopK, setSelectedTopK] = useState(settings.top_k_default);
  const [showCustom, setShowCustom] = useState(false);
  const [customValue, setCustomValue] = useState("");
  const [showCustomDepth, setShowCustomDepth] = useState(false);
  const [customDepthValue, setCustomDepthValue] = useState("");
  const [copiedIndex, setCopiedIndex] = useState(null);
  const [scopeNotice, setScopeNotice] = useState(null);
  const [modules, setModules] = useState([]);
  const [developments, setDevelopments] = useState([]);
  const [localeFacets, setLocaleFacets] = useState([]);
  // Исключающий scope-фильтр (Этап 4a.1, развитие плана): активен не более один из
  // moduleFilter / devFilter — иначе backend-OR даёт объединение, а не пересечение.
  const [moduleFilter, setModuleFilter] = useState("");
  const [devFilter, setDevFilter] = useState(null);
  // Фильтр по языку документа: "" = все, "unknown" = «не определён», иначе код.
  const [sourceLocale, setSourceLocale] = useState("");
  const logRef = useRef(null);
  const activeRequestRef = useRef(null);
  const stopCurrentRef = useRef(null);
  const copyTimerRef = useRef(null);

  useEffect(() => {
    return () => {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    listAttributeValues("module")
      .then((vals) => {
        if (!cancelled) setModules(vals.map((v) => v.value));
      })
      .catch(() => {});
    listDevelopments()
      .then((devs) => {
        if (!cancelled) setDevelopments(devs);
      })
      .catch(() => {});
    getSourceLocaleFacets()
      .then((items) => {
        if (!cancelled) setLocaleFacets(items);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  // Контекст «поиск → загрузка» (Этап 4a.1): сопоставляет активные теги фильтра
  // со справочником модулей/разработок. Возвращает ссылку на префилл загрузки.
  const resolveUploadHint = (filterTags) => {
    const set = new Set(filterTags || []);
    const dev = developments.find((d) => set.has(d.number) || set.has(d.name));
    if (dev) {
      return {
        href: `/?upload_dev=${dev.id}`,
        label: t("chat.uploadHintDev", {
          number: dev.number,
          name: (dev.display_name || dev.name) ? ` · ${dev.display_name || dev.name}` : "",
        }),
      };
    }
    // moduleName, а не module: имя `module` затеняет одноимённую переменную
    // CommonJS-обёртки (@next/next/no-assign-module-variable).
    const moduleName = modules.find((m) => set.has(m));
    if (moduleName) {
      return {
        href: `/?upload_module=${encodeURIComponent(moduleName)}`,
        label: t("chat.uploadHintModule", { module: moduleName }),
      };
    }
    return null;
  };

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [messages.length]);

  const copyAnswer = async (index, text) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      return;
    }
    setCopiedIndex(index);
    if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    copyTimerRef.current = setTimeout(() => setCopiedIndex(null), 2000);
  };

  const clampTopK = (value) => {
    const n = Math.round(value);
    if (Number.isNaN(n)) return settings.top_k_default;
    return Math.min(settings.top_k_max, Math.max(settings.top_k_min, n));
  };

  const applyCustom = () => {
    const n = parseInt(customValue, 10);
    if (!Number.isNaN(n)) {
      const clamped = clampTopK(n);
      setSelectedTopK(clamped);
      setCustomValue(String(clamped));
    } else {
      setCustomValue(String(selectedTopK));
    }
  };

  const selectPreset = (preset) => {
    setSelectedTopK(preset);
    setShowCustom(false);
    setCustomValue("");
  };

  // Scope-тег: модуль ИЛИ номер разработки (ровно один, взаимоисключающие).
  const selectedDev = developments.find((d) => d.id === Number(devFilter)) || null;
  // Если выбран модуль — в списке разработок показываем только разработки этого
  // модуля (иначе можно было бы выбрать разработку из другого модуля, что ломало
  // бы семантику исключающего scope-фильтра).
  const scopeDevelopments = moduleFilter
    ? developments.filter((d) => d.module === moduleFilter)
    : developments;
  const devNumber = selectedDev ? String(selectedDev.number) : "";
  const effectiveTags = Array.from(
    new Set([...tags, ...(moduleFilter ? [moduleFilter] : []), ...(devNumber ? [devNumber] : [])])
  );
  const localeOptions = [
    { value: "", label: t("docs.allLocales") },
    ...facetOptions(localeFacets, locale).map((option) => ({
      value: option.code,
      label: option.code === "unknown" ? t("docs.localeUnknown") : `${option.label} (${option.count})`,
      searchText: `${option.code} ${option.label}`,
    })),
  ];
  if (sourceLocale && !localeOptions.some((option) => option.value === sourceLocale)) {
    localeOptions.push({ value: sourceLocale, label: sourceLocale, searchText: sourceLocale });
  }

  const stopCurrent = () => {
    const active = activeRequestRef.current;
    if (!active) return;
    cancelChatAttempt(active.id, active.sessionId).catch(() => {});
    active.controller.abort();
    activeRequestRef.current = null;
    setPending(false);
    setMessages((items) => items.map((item) =>
      item.attemptId === active.id ? { ...item, stopped: true, text: t("chat.answerStopped") } : item
    ));
  };
  stopCurrentRef.current = stopCurrent;

  useEffect(() => {
    const stopOnLeave = () => stopCurrentRef.current?.();
    window.addEventListener("pagehide", stopOnLeave);
    return () => {
      window.removeEventListener("pagehide", stopOnLeave);
      stopCurrentRef.current?.();
    };
  }, []);

  const normalizeDepth = (value) => {
    const number = Number(value);
    return value === "" || !Number.isFinite(number) ? searchDepth
      : Math.min(settings.search_depth_max, Math.max(settings.search_depth_min, Math.trunc(number)));
  };

  const applyCustomDepth = () => {
    const depth = normalizeDepth(customDepthValue);
    setSearchDepth(depth);
    setCustomDepthValue(String(depth));
    return depth;
  };

  const sendQuestion = async (q, requestOptions, glossary = useGlossary) => {
    stopCurrent();
    const attemptId = crypto.randomUUID();
    const targetSessionId = sessionId || crypto.randomUUID();
    if (!sessionId) setSessionId(targetSessionId);
    const controller = new AbortController();
    activeRequestRef.current = { id: attemptId, sessionId: targetSessionId, controller };
    const uploadHint = resolveUploadHint(requestOptions.requestTags);
    setMessages((items) => [
      ...items,
      { role: "user", text: q, query: q, ...requestOptions },
      { role: "assistant", attemptId, text: t("chat.thinking"), sources: [], query: q, ...requestOptions },
    ]);
    setPending(true);
    try {
      const resp = await chat(
        q, requestOptions.requestTags, requestOptions.requestTopK,
        requestOptions.requestMode, targetSessionId, requestOptions.requestSourceLocale,
        glossary, requestOptions.requestMailMode,
        (text) => setMessages((items) => applyAnswerEvent(items, { attemptId, type: "delta", text })),
        (sources) => setMessages((items) => applyAnswerEvent(items, { attemptId, type: "sources", sources })),
        {
          responseMode: requestOptions.responseMode,
          searchDepth: requestOptions.requestSearchDepth,
          sourceSelection: requestOptions.requestSourceSelection,
          searchDocIds: requestOptions.requestDocIds,
          attemptId,
          signal: controller.signal,
          onProgress: (event) => setMessages((items) => applyAnswerEvent(items, { ...event, attemptId })),
        },
      );
      if (activeRequestRef.current?.id !== attemptId) return;
      if (resp.session_id) setSessionId(resp.session_id);
      setMessages((items) => {
        if (items.at(-1)?.attemptId !== attemptId) return items;
        const copy = [...items];
        copy[copy.length - 1] = {
          ...copy.at(-1), text: resp.answer, sources: resp.sources,
          requestSearchDepth: resp.search_depth ?? requestOptions.requestSearchDepth,
          searchLimitReached: Boolean(resp.search_limit_reached),
          uploadHint, applied_terms: resp.applied_terms, expansion_status: resp.expansion_status,
        };
        return copy;
      });
    } catch (err) {
      if (activeRequestRef.current?.id !== attemptId) return;
      setMessages((items) => {
        if (items.at(-1)?.attemptId !== attemptId) return items;
        const copy = [...items];
        copy[copy.length - 1] = {
          ...copy.at(-1), text: controller.signal.aborted ? t("chat.answerStopped") : t("chat.errorPrefix", { message: friendlyApiError(err, t) }),
          stopped: controller.signal.aborted,
        };
        return copy;
      });
    } finally {
      if (activeRequestRef.current?.id === attemptId) {
        activeRequestRef.current = null;
        setPending(false);
      }
    }
  };

  const send = async (e) => {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    if (searchScopeEnabled && !searchScopeDocuments.length) return;
    const requestOptions = { requestMailMode: mailMode, requestTags: effectiveTags, requestTopK: selectedTopK, requestSearchDepth: showCustomDepth ? applyCustomDepth() : searchDepth, requestMode: selectedMode, requestSourceLocale: sourceLocale, responseMode: settings.response_modes?.includes(responseMode) ? responseMode : null, requestDocIds: scopeRequestIds(searchScopeDocuments, searchScopeEnabled) };
    setQuery("");
    await sendQuestion(q, requestOptions);
  };

  const selectSources = (messageIndex, indexes, checked) => {
    setMessages((items) => items.map((message, index) => index === messageIndex ? {
      ...message, selectedSourceIndexes: updateSelection(message.selectedSourceIndexes ?? [], indexes, checked, selectableSources(message)),
    } : message));
  };

  const answerSelected = (message) => sendQuestion(message.query, {
    requestMailMode: message.requestMailMode, requestTags: message.requestTags,
    requestTopK: message.requestTopK, requestMode: message.requestMode,
    requestSearchDepth: message.requestSearchDepth, requestSourceLocale: message.requestSourceLocale,
    responseMode: "full", requestSourceSelection: {
      attempt_id: message.attemptId, indexes: [...(message.selectedSourceIndexes ?? [])],
    },
  }, false);

  const addSelectedToScope = (message) => {
    try {
      const next = addScopeDocuments(searchScopeDocuments, message.sources, message.selectedSourceIndexes ?? []);
      setScopeNotice({ key: "chat.scopeAdded", count: next.length - searchScopeDocuments.length });
      setSearchScopeDocuments(next);
    } catch (error) {
      if (!(error instanceof RangeError)) throw error;
      setScopeNotice({ key: "chat.scopeLimit", max: SEARCH_SCOPE_MAX_DOCUMENTS });
    }
  };

  const repeatWithoutGlossary = async (message) => {
    if (!message.query) return;
    const q = message.query;
    const requestOptions = { requestMailMode: retryMailMode(message), requestTags: message.requestTags ?? effectiveTags, requestTopK: message.requestTopK ?? selectedTopK, requestSearchDepth: message.requestSearchDepth ?? searchDepth, requestMode: message.requestMode ?? selectedMode, requestSourceLocale: message.requestSourceLocale ?? sourceLocale, responseMode: message.responseMode ?? (settings.response_modes?.includes(responseMode) ? responseMode : null), requestDocIds: message.requestDocIds, requestSourceSelection: message.requestSourceSelection };
    await sendQuestion(q, requestOptions, false);
  };

  return (
    <section className="panel">
      <div className="chat-toolbar">
        {settings.response_modes?.length > 0 && (
          <div className="mode-picker chat-response-modes" role="radiogroup" aria-label={t("chat.responseModeLabel")}>
            {settings.response_modes.map((mode) => (
              <button key={mode} type="button" role="radio" aria-checked={responseMode === mode}
                className={`mode-btn ${responseMode === mode ? "active" : ""}`}
                title={t(`chat.responseMode.${mode}.description`)}
                onClick={() => setResponseMode(mode)}>
                {t(`chat.responseMode.${mode}.label`)}
              </button>
            ))}
          </div>
        )}
        <div className="chat-toolbar-actions">
          <label className="chat-scope-toggle">
            <input type="checkbox" checked={searchScopeEnabled} onChange={(event) => setSearchScopeEnabled(event.target.checked)} />
            {t("chat.scopeOnly")} <span className="meta">({searchScopeDocuments.length})</span>
          </label>
          <Link href="/chat/history" className="btn ghost">
            {t("chat.historyBtn")}
          </Link>
          <button
            type="button"
            className="btn ghost"
            onClick={() => { stopCurrent(); startNewChat(); setScopeNotice(null); }}
            disabled={messages.length === 0 && searchScopeDocuments.length === 0 && !searchScopeEnabled}
            title={t("chat.newChatTitle")}
          >
            {t("chat.newChat")}
          </button>
        </div>
      </div>
      {searchScopeEnabled && <ChatSearchScope documents={searchScopeDocuments}
        onRemove={(docId) => { setSearchScopeDocuments((items) => items.filter((document) => document.doc_id !== docId)); setScopeNotice(null); }}
        onClear={() => { setSearchScopeDocuments([]); setScopeNotice(null); }}
        refreshKey={messages.length} />}
      {scopeNotice && <div className="meta" role="status">{t(scopeNotice.key, scopeNotice)}</div>}
      <div className="chat-log-frame">
      <div className="chat-log" ref={logRef}>
      <div className="chat-log-content">
        {messages.map((m, i) => {
          const selectedSources = new Set(m.selectedSourceIndexes ?? []);
          const availableSources = selectableSources(m);
          const availableIndexes = new Set(availableSources.map((source) => source.source_index));
          return (
          <div key={i} className={`msg ${m.role}`} data-request-index={m.role === "user" ? i : undefined}>
            <div className="role-row">
              <div className="role">{m.role === "user" ? t("chat.you") : t("chat.assistant")}</div>
              {m.role === "assistant" && (
                <button
                  type="button"
                  className="copy-btn"
                  disabled={pending && i === messages.length - 1}
                  onClick={() => copyAnswer(i, m.text)}
                  title={t("chat.copyAnswer")}
                >
                  {copiedIndex === i ? <CheckIcon size={14} /> : <CopyIcon size={14} />}
                  {copiedIndex === i ? t("chat.copied") : t("chat.copy")}
                </button>
              )}
            </div>
            <div className="bubble">
              {m.role === "assistant" ? (
                <MarkdownViewer
                  className="okf-markdown chat-markdown"
                  text={m.text}
                  remarkPlugins={[remarkCiteLinks]}
                  components={{
                    a: (props) => <CiteLink sources={m.sources || []} {...props} />,
                  }}
                />
              ) : (
                m.text
              )}
            </div>
            {m.role === "assistant" && availableSources.length > 0 && (
              <div className="source-selection-actions">
                <button type="button" className="btn ghost" onClick={() => selectSources(i, availableSources.map((source) => source.source_index), true)}>{t("chat.selectAllSources")}</button>
                <button type="button" className="btn ghost" onClick={() => selectSources(i, m.sources.map((source) => source.source_index), false)} disabled={!m.selectedSourceIndexes?.length}>{t("chat.clearSourceSelection")}</button>
                <button type="button" className="btn" disabled={pending || !m.selectedSourceIndexes?.length} onClick={() => answerSelected(m)}>
                  {t("chat.answerSelected", { count: m.selectedSourceIndexes?.length ?? 0 })}
                </button>
                <button type="button" className="btn ghost" disabled={!m.selectedSourceIndexes?.length} onClick={() => addSelectedToScope(m)}>{t("chat.scopeAddSelected")}</button>
                <span className="meta">{t("chat.selectionDescription")}</span>
              </div>
            )}
            {m.role === "assistant" && m.sources?.length > 0 && (
              <ul className="chat-document-list">
                {groupSourcesByDocument(m.sources).map((group) => (
                  <li key={group.doc_id}>
                    {group.sources.some((source) => availableIndexes.has(source.source_index)) && <SelectionCheckbox state={selectionState(selectedSources, group.sources.filter((source) => availableIndexes.has(source.source_index)).map((source) => source.source_index))}
                      label={t("chat.selectDocument", { name: group.filename })}
                      onChange={(checked) => selectSources(i, group.sources.map((source) => source.source_index), checked)} />}
                    {documentHref(group.source) ? (
                      <Link href={documentHref(group.source)}>{group.filename}</Link>
                    ) : group.filename}
                    <span className="meta"> · {group.sources.length} {t("chat.fragments")}</span>
                  </li>
                ))}
              </ul>
            )}
            {m.role === "assistant" && m.requestSourceSelection && (
              <div className="meta">{t("chat.selectedAnswerContext", { count: m.requestSourceSelection.indexes.length })}</div>
            )}
            {m.role === "assistant" && m.requestDocIds != null && (
              <div className="meta">{t("chat.scopeUsed", { count: m.requestDocIds.length })}</div>
            )}
            {m.role === "assistant" && m.searchLimitReached && (
              <div className="meta" role="status">
                {t(m.requestSearchDepth >= settings.search_depth_max ? "chat.searchLimitReachedMax" : "chat.searchLimitReached", { depth: m.requestSearchDepth })}
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
            {m.role === "assistant" && m.progress?.phase && pending && i === messages.length - 1 && (
              <div className="chat-progress" role="status">
                {m.progress.phase === "synthesis"
                  ? t("chat.synthesizing")
                  : t("chat.batchProgress", { done: m.progress.batches_done ?? 0, total: m.progress.batches_total ?? 0 })}
              </div>
            )}
            {m.role === "assistant" && pending && i === messages.length - 1 && (
              <button type="button" className="btn ghost" onClick={stopCurrent}>{t("chat.stopAnswer")}</button>
            )}
            {m.role === "assistant" && m.stopped && (
              <button type="button" className="btn ghost" onClick={() => sendQuestion(m.query, {
                requestMailMode: m.requestMailMode, requestTags: m.requestTags,
                requestTopK: m.requestTopK, requestMode: m.requestMode,
                requestSearchDepth: m.requestSearchDepth,
                requestSourceSelection: m.requestSourceSelection,
                requestDocIds: m.requestDocIds,
                requestSourceLocale: m.requestSourceLocale, responseMode: m.responseMode,
              })}>{t("chat.restartAnswer")}</button>
            )}
            {m.role === "assistant" && <AppliedTerms status={m.expansion_status} appliedTerms={m.applied_terms} />}
            {m.role === "assistant" && m.applied_terms?.length > 0 && (
              <button type="button" className="btn ghost glossary-repeat" onClick={() => repeatWithoutGlossary(m)} disabled={pending}>
                {t("chat.glossary.repeatWithout")}
              </button>
            )}
            {m.sources && m.sources.length > 0 && (
              <details
                className="sources"
                open={Boolean(m.sourcesOpen)}
                onToggle={(event) => {
                  const open = event.currentTarget.open;
                  setMessages((items) => {
                    if (!items[i] || items[i].sourcesOpen === open) return items;
                    const copy = [...items];
                    copy[i] = { ...copy[i], sourcesOpen: open, sourcesTouched: true };
                    return copy;
                  });
                }}
              >
                <summary>{t("chat.sources")}</summary>
                <ol style={{ "--source-number-digits": String(m.sources.length).length }}>
                  {m.sources.map((s, j) => {
                    const href = sourceHref(s);
                    const isChunk = s.point_type === "chunk";
                    const badge = isChunk ? "\u{1F4E6}" : "\u{1F4C4}";
                    return (
                      <li key={j}>
                        {availableIndexes.has(s.source_index) && (
                          <SelectionCheckbox state={selectionState(selectedSources, [s.source_index])}
                            label={t("chat.selectFragment", { index: s.source_index, name: s.title })}
                            onChange={(checked) => selectSources(i, [s.source_index], checked)} />
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
                        {" "}<span className="meta">{t(m.responseMode === "documents" ? "chat.sourceFound" : inModelContext(s) ? "chat.sourceInContext" : "chat.sourceSearchOnly")}</span>
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
                  })}
                </ol>
              </details>
            )}
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
        })}
      </div>
      </div>
      <ChatRequestNavigation messages={messages} logRef={logRef} />
      </div>
      <details className="search-settings">
        <summary>{t("chat.searchSettings")}</summary>
        {settings.response_modes?.length > 0 && (
          <div className="topk-picker">
            <span className="topk-label" title={t("chat.searchDepthDescription")}>{t("chat.searchDepthLabel")}</span>
            <div className="topk-picker" style={{ marginBottom: 0 }} role="radiogroup" aria-label={t("chat.searchDepthLabel")}>
              {settings.search_depth_presets.map((depth) => (
                <button key={depth} type="button" role="radio" aria-checked={searchDepth === depth && !showCustomDepth}
                  className={`topk-btn ${searchDepth === depth && !showCustomDepth ? "active" : ""}`}
                  onClick={() => { setSearchDepth(depth); setShowCustomDepth(false); }}>
                  {depth}
                </button>
              ))}
            </div>
            <button type="button" className="topk-btn" aria-pressed={showCustomDepth}
              onClick={() => { setCustomDepthValue(String(searchDepth)); setShowCustomDepth((value) => !value); }}>
              {t("chat.topkOther")}{!settings.search_depth_presets.includes(searchDepth) ? ` (${searchDepth})` : ""}
            </button>
            {showCustomDepth && (
              <input type="number" className="topk-custom" aria-label={t("chat.searchDepthLabel")}
                min={settings.search_depth_min} max={settings.search_depth_max} step="1"
                value={customDepthValue} onChange={(event) => setCustomDepthValue(event.target.value)}
                onBlur={applyCustomDepth} onKeyDown={(event) => {
                  if (event.key === "Enter") { event.preventDefault(); applyCustomDepth(); }
                }} />
            )}
          </div>
        )}
        {settings.glossary_query_expansion_enabled === false && (
          <div className="glossary-status-warning" role="status">{t("chat.glossary.disabled")}</div>
        )}
        <label className="glossary-toggle"><input type="checkbox" checked={useGlossary} disabled={!settings.glossary_query_expansion_enabled} onChange={(e) => setUseGlossary(e.target.checked)} /> {t("chat.glossary.toggle")}</label>
        <div className="mode-picker" role="radiogroup" aria-label={t("chat.modePickerAria")}>
        {settings.search_modes.map((mode) => (
          <button
            key={mode}
            type="button"
            role="radio"
            aria-checked={selectedMode === mode}
            className={`mode-btn ${selectedMode === mode ? "active" : ""}`}
            onClick={() => setSelectedMode(mode)}
          >
            {MODE_LABELS[mode] ?? mode}
          </button>
        ))}
      </div>
      {settings.response_modes?.length === 0 && <div className="topk-picker" role="radiogroup" aria-label={t("chat.topkLabel")}>
        <span className="topk-label">{t("chat.resultsLabel")}</span>
        {settings.top_k_presets.map((preset) => (
          <button
            key={preset}
            type="button"
            role="radio"
            aria-checked={selectedTopK === preset}
            className={`topk-btn ${selectedTopK === preset ? "active" : ""}`}
            onClick={() => selectPreset(preset)}
          >
            {preset} — {getPresetLabel(preset, settings, t)}
          </button>
        ))}
        <button
          type="button"
          className="topk-btn"
          aria-pressed={showCustom}
          onClick={() => setShowCustom((v) => !v)}
        >
          {t("chat.topkOther")}
        </button>
        {showCustom && (
          <input
            type="number"
            className="topk-custom"
            min={settings.top_k_min}
            max={settings.top_k_max}
            value={customValue}
            placeholder={String(selectedTopK)}
            onChange={(e) => setCustomValue(e.target.value)}
            onBlur={applyCustom}
            onKeyDown={(e) => {
              if (e.key === "Enter") applyCustom();
            }}
          />
        )}
      </div>}
      </details>
      <TagPicker
        label={t("chat.tagsLabel")}
        placeholder={t("chat.tagsPlaceholder")}
        selected={tags}
        onChange={setTags}
      />
      <div className="chat-scope-filter">
        <span className="tag-picker-label">{t("chat.moduleLabel")}</span>
        <ModulePicker
          modules={modules}
          value={moduleFilter}
          username={user?.username || ""}
          onChange={(v) => {
            setModuleFilter(v);
            if (v) setDevFilter(null);
          }}
        />
        <span className="tag-picker-label">{t("chat.developmentLabel")}</span>
        <DevelopmentFilter
          developments={scopeDevelopments}
          value={devFilter}
          onChange={(devId) => {
            setDevFilter(devId);
            if (devId != null) setModuleFilter("");
          }}
        />
        <span className="tag-picker-label">{t("chat.localeLabel")}</span>
        <SearchableSelect
          options={localeOptions}
          value={sourceLocale}
          onChange={setSourceLocale}
          searchPlaceholder={t("docs.localeSearchPlaceholder")}
          emptyLabel={t("docs.empty")}
          ariaLabel={t("docs.localeFilterAria")}
        />
        <label htmlFor="chat-mail-mode" className="tag-picker-label">{t("chat.mailModeLabel")}</label>
        <select id="chat-mail-mode" value={mailMode} onChange={(e) => setMailMode(e.target.value)} aria-describedby="chat-mail-hint chat-mail-unknown-hint">
          <option value="all">{t("chat.mailModeAll")}</option>
          <option value="exclude">{t("chat.mailModeExclude")}</option>
          <option value="only">{t("chat.mailModeOnly")}</option>
        </select>
        <span id="chat-mail-hint" className="chat-mail-hint">{t("chat.mailModeHint")}</span>
        <span id="chat-mail-unknown-hint" className="chat-mail-hint">{mailMode !== "all" ? t("chat.mailModeUnknownHint") : ""}</span>
      </div>
      <form className="chat-form" onSubmit={send}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("chat.queryPlaceholder")}
          autoComplete="off"
        />
        <button type="submit" disabled={searchScopeEnabled && searchScopeDocuments.length === 0}>
          {t("chat.send")}
        </button>
      </form>
    </section>
  );
}
