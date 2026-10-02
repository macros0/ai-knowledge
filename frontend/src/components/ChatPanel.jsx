"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { chat, cancelChatAttempt, friendlyApiError, getSourceLocaleFacets, listAttributeValues, listDevelopments } from "@/lib/api";
import { applyAnswerEvent, toggleChatSources } from "@/lib/chatAnswerState.mjs";
import { createChatDeltaBuffer, reconcileChatSources } from "@/lib/chatRenderState.mjs";
import { updateSelection, selectableSources } from "@/lib/chatSourceSelection.mjs";
import { facetOptions } from "@/lib/sourceLocales.mjs";
import { addScopeDocuments, scopeRequestIds, SEARCH_SCOPE_MAX_DOCUMENTS } from "@/lib/chatSearchScope.mjs";
import { attachChatScroll } from "@/lib/chatScrollPosition.mjs";
import TagPicker from "./TagPicker";
import DevelopmentFilter from "./DevelopmentFilter";
import ModulePicker from "./ModulePicker";
import { retryMailMode } from "@/lib/chatMailFilter.mjs";
import { useChat } from "@/context/ChatContext";
import { useAuth } from "@/context/AuthContext";
import { useI18n } from "@/i18n/LocaleContext";
import SearchableSelect from "./SearchableSelect";
import ChatSearchScope from "./ChatSearchScope";
import ChatRequestNavigation from "./ChatRequestNavigation";
import ChatComposer from "./ChatComposer";
import ChatMessageView from "./ChatMessageView";

function getPresetLabel(preset, settings, t) {
  if (preset === settings.top_k_default) return t("chat.topkStandard");
  const minPreset = Math.min(...settings.top_k_presets);
  const maxPreset = Math.max(...settings.top_k_presets);
  if (preset === minPreset) return t("chat.topkShort");
  if (preset === maxPreset) return t("chat.topkDetailed");
  return String(preset);
}

export default function ChatPanel() {
  const { messages, tags, pending, settings, selectedMode, searchDepth, setSearchDepth, sessionId, scrollPositionRef, mailMode, setMailMode, useGlossary, setUseGlossary, setSessionId, startNewChat, setMessages, setTags, setPending, setSelectedMode, MODE_LABELS, searchScopeDocuments, setSearchScopeDocuments, searchScopeEnabled, setSearchScopeEnabled,
    responseMode, setResponseMode, selectedTopK, setSelectedTopK, showCustom, setShowCustom, customValue, setCustomValue,
    showCustomDepth, setShowCustomDepth, customDepthValue, setCustomDepthValue, moduleFilter, setModuleFilter, devFilter, setDevFilter,
    sourceLocale, setSourceLocale, searchSettingsOpen, setSearchSettingsOpen } = useChat();
  const { user } = useAuth();
  const { t, locale } = useI18n();
  const [copiedIndex, setCopiedIndex] = useState(null);
  const [scopeNotice, setScopeNotice] = useState(null);
  const [modules, setModules] = useState([]);
  const [developments, setDevelopments] = useState([]);
  const [localeFacets, setLocaleFacets] = useState([]);
  const logRef = useRef(null);
  const activeRequestRef = useRef(null);
  const stopCurrentRef = useRef(null);
  const copyTimerRef = useRef(null);
  const lastAssistantIndex = messages.findLastIndex((message) => message.role === "assistant");

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
  const resolveUploadHint = useCallback((filterTags) => {
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
  }, [developments, modules, t]);

  useLayoutEffect(() => {
    if (!logRef.current) return;
    return attachChatScroll(logRef.current, scrollPositionRef, { sessionId, messageCount: messages.length });
  }, [messages.length, sessionId, scrollPositionRef]);

  const copyAnswer = useCallback(async (index, text) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      return;
    }
    setCopiedIndex(index);
    if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    copyTimerRef.current = setTimeout(() => setCopiedIndex(null), 2000);
  }, []);

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
  const effectiveTags = useMemo(() => Array.from(
    new Set([...tags, ...(moduleFilter ? [moduleFilter] : []), ...(devNumber ? [devNumber] : [])])
  ), [tags, moduleFilter, devNumber]);
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

  const stopCurrent = useCallback(() => {
    const active = activeRequestRef.current;
    if (!active) return;
    active.deltaBuffer.discard();
    cancelChatAttempt(active.id, active.sessionId).catch(() => {});
    active.controller.abort();
    activeRequestRef.current = null;
    setPending(false);
    setMessages((items) => items.map((item) =>
      item.attemptId === active.id ? { ...item, stopped: true, text: t("chat.answerStopped") } : item
    ));
  }, [setMessages, setPending, t]);
  stopCurrentRef.current = stopCurrent;

  useEffect(() => {
    const stopOnLeave = () => stopCurrentRef.current?.();
    window.addEventListener("pagehide", stopOnLeave);
    return () => {
      window.removeEventListener("pagehide", stopOnLeave);
      stopCurrentRef.current?.();
    };
  }, []);

  const normalizeDepth = useCallback((value) => {
    const number = Number(value);
    return value === "" || !Number.isFinite(number) ? searchDepth
      : Math.min(settings.search_depth_max, Math.max(settings.search_depth_min, Math.trunc(number)));
  }, [searchDepth, settings.search_depth_max, settings.search_depth_min]);

  const applyCustomDepth = useCallback(() => {
    const depth = normalizeDepth(customDepthValue);
    setSearchDepth(depth);
    setCustomDepthValue(String(depth));
    return depth;
  }, [customDepthValue, normalizeDepth, setCustomDepthValue, setSearchDepth]);

  const sendQuestion = useCallback(async (q, requestOptions, glossary = useGlossary) => {
    stopCurrent();
    const attemptId = crypto.randomUUID();
    const targetSessionId = sessionId || crypto.randomUUID();
    if (!sessionId) setSessionId(targetSessionId);
    const controller = new AbortController();
    const deltaBuffer = createChatDeltaBuffer({ onFlush: (text) => {
      if (activeRequestRef.current?.id === attemptId) {
        setMessages((items) => applyAnswerEvent(items, { attemptId, type: "delta", text }));
      }
    } });
    activeRequestRef.current = { id: attemptId, sessionId: targetSessionId, controller, deltaBuffer };
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
        (text) => deltaBuffer.append(text),
        (sources) => {
          deltaBuffer.flush();
          setMessages((items) => applyAnswerEvent(items, { attemptId, type: "sources", sources }));
        },
        {
          responseMode: requestOptions.responseMode,
          searchDepth: requestOptions.requestSearchDepth,
          sourceSelection: requestOptions.requestSourceSelection,
          searchDocIds: requestOptions.requestDocIds,
          attemptId,
          signal: controller.signal,
          onProgress: (event) => {
            deltaBuffer.flush();
            setMessages((items) => applyAnswerEvent(items, { ...event, attemptId }));
          },
        },
      );
      deltaBuffer.discard();
      if (activeRequestRef.current?.id !== attemptId) return;
      if (resp.session_id) setSessionId(resp.session_id);
      setMessages((items) => {
        if (items.at(-1)?.attemptId !== attemptId) return items;
        const copy = [...items];
        copy[copy.length - 1] = {
          ...copy.at(-1), text: resp.answer, sources: reconcileChatSources(copy.at(-1).sources, resp.sources),
          requestSearchDepth: resp.search_depth ?? requestOptions.requestSearchDepth,
          searchLimitReached: Boolean(resp.search_limit_reached),
          uploadHint, applied_terms: resp.applied_terms, expansion_status: resp.expansion_status,
        };
        return copy;
      });
    } catch (err) {
      deltaBuffer.discard();
      if (activeRequestRef.current?.id !== attemptId) return;
      setMessages((items) => {
        if (items.at(-1)?.attemptId !== attemptId) return items;
        const copy = [...items];
        copy[copy.length - 1] = {
          ...copy.at(-1), text: controller.signal.aborted ? t("chat.answerStopped") : t("chat.errorPrefix", { message: friendlyApiError(err, t) }),
          stopped: controller.signal.aborted,
          requestId: err.requestId, localReportId: err.localReportId,
        };
        return copy;
      });
    } finally {
      deltaBuffer.discard();
      if (activeRequestRef.current?.id === attemptId) {
        activeRequestRef.current = null;
        setPending(false);
      }
    }
  }, [resolveUploadHint, sessionId, setMessages, setPending, setSessionId, stopCurrent, t, useGlossary]);

  const send = useCallback(async (q) => {
    if (searchScopeEnabled && !searchScopeDocuments.length) return;
    const requestOptions = { requestMailMode: mailMode, requestTags: effectiveTags, requestTopK: selectedTopK, requestSearchDepth: showCustomDepth ? applyCustomDepth() : searchDepth, requestMode: selectedMode, requestSourceLocale: sourceLocale, responseMode: settings.response_modes?.includes(responseMode) ? responseMode : null, requestDocIds: scopeRequestIds(searchScopeDocuments, searchScopeEnabled) };
    await sendQuestion(q, requestOptions);
  }, [applyCustomDepth, effectiveTags, mailMode, responseMode, searchDepth, searchScopeDocuments,
      searchScopeEnabled, selectedMode, selectedTopK, sendQuestion, settings.response_modes,
      showCustomDepth, sourceLocale]);

  const selectSources = useCallback((messageIndex, indexes, checked) => {
    setMessages((items) => items.map((message, index) => index === messageIndex ? {
      ...message, selectedSourceIndexes: updateSelection(message.selectedSourceIndexes ?? [], indexes, checked, selectableSources(message)),
    } : message));
  }, [setMessages]);

  const answerSelected = useCallback((message) => sendQuestion(message.query, {
    requestMailMode: message.requestMailMode, requestTags: message.requestTags,
    requestTopK: message.requestTopK, requestMode: message.requestMode,
    requestSearchDepth: message.requestSearchDepth, requestSourceLocale: message.requestSourceLocale,
    responseMode: "full", requestSourceSelection: {
      attempt_id: message.attemptId, indexes: [...(message.selectedSourceIndexes ?? [])],
    },
  }, false), [sendQuestion]);

  const addSelectedToScope = useCallback((message) => {
    try {
      const next = addScopeDocuments(searchScopeDocuments, message.sources, message.selectedSourceIndexes ?? []);
      setScopeNotice({ key: "chat.scopeAdded", count: next.length - searchScopeDocuments.length });
      setSearchScopeDocuments(next);
    } catch (error) {
      if (!(error instanceof RangeError)) throw error;
      setScopeNotice({ key: "chat.scopeLimit", max: SEARCH_SCOPE_MAX_DOCUMENTS });
    }
  }, [searchScopeDocuments, setSearchScopeDocuments]);

  const repeatWithoutGlossary = useCallback(async (message) => {
    if (!message.query) return;
    const q = message.query;
    const requestOptions = { requestMailMode: retryMailMode(message), requestTags: message.requestTags ?? effectiveTags, requestTopK: message.requestTopK ?? selectedTopK, requestSearchDepth: message.requestSearchDepth ?? searchDepth, requestMode: message.requestMode ?? selectedMode, requestSourceLocale: message.requestSourceLocale ?? sourceLocale, responseMode: message.responseMode ?? (settings.response_modes?.includes(responseMode) ? responseMode : null), requestDocIds: message.requestDocIds, requestSourceSelection: message.requestSourceSelection };
    await sendQuestion(q, requestOptions, false);
  }, [effectiveTags, responseMode, searchDepth, selectedMode, selectedTopK, sendQuestion, settings.response_modes, sourceLocale]);

  const retryAnswer = useCallback((message) => sendQuestion(message.query, {
    requestMailMode: message.requestMailMode, requestTags: message.requestTags,
    requestTopK: message.requestTopK, requestMode: message.requestMode,
    requestSearchDepth: message.requestSearchDepth, requestSourceSelection: message.requestSourceSelection,
    requestDocIds: message.requestDocIds, requestSourceLocale: message.requestSourceLocale,
    responseMode: message.responseMode,
  }), [sendQuestion]);

  const toggleSources = useCallback((index, open) => setMessages((items) => toggleChatSources(items, index, open)), [setMessages]);

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
        {messages.map((message, index) => <ChatMessageView key={index} message={message} index={index}
          isLatest={index === lastAssistantIndex}
          isPending={pending && index === messages.length - 1} actionsPending={pending}
          isCopied={copiedIndex === index} searchDepthMax={settings.search_depth_max}
          onCopy={copyAnswer} onSelect={selectSources} onAnswerSelected={answerSelected}
          onAddToScope={addSelectedToScope} onRetry={retryAnswer} onRepeatWithoutGlossary={repeatWithoutGlossary}
          onStop={stopCurrent} onToggleSources={toggleSources} />)}
      </div>
      </div>
      <ChatRequestNavigation messages={messages} logRef={logRef} />
      </div>
      <details className="search-settings" open={searchSettingsOpen} onToggle={(event) => setSearchSettingsOpen(event.currentTarget.open)}>
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
      <ChatComposer disabled={searchScopeEnabled && searchScopeDocuments.length === 0} onSubmit={send} />
    </section>
  );
}
