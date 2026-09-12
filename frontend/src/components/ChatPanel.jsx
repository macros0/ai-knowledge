"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { chat, friendlyApiError, getSourceLocaleFacets, listAttributeValues, listDevelopments } from "@/lib/api";
import { CiteLink, remarkCiteLinks, sourceHref } from "@/lib/chatSources";
import { facetOptions } from "@/lib/sourceLocales.mjs";
import TagPicker from "./TagPicker";
import DevelopmentFilter from "./DevelopmentFilter";
import ModulePicker from "./ModulePicker";
import MarkdownViewer from "./MarkdownViewer";
import { CheckIcon, CopyIcon } from "./icons";
import { useChat } from "@/context/ChatContext";
import { useAuth } from "@/context/AuthContext";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";
import AppliedTerms from "./AppliedTerms";

function getPresetLabel(preset, settings, t) {
  if (preset === settings.top_k_default) return t("chat.topkStandard");
  const minPreset = Math.min(...settings.top_k_presets);
  const maxPreset = Math.max(...settings.top_k_presets);
  if (preset === minPreset) return t("chat.topkShort");
  if (preset === maxPreset) return t("chat.topkDetailed");
  return String(preset);
}

export default function ChatPanel() {
  const { messages, tags, pending, settings, selectedMode, sessionId, useGlossary, setUseGlossary, setSessionId, startNewChat, setMessages, setTags, setPending, setSelectedMode, MODE_LABELS } = useChat();
  const { user } = useAuth();
  const { showToast } = useToast();
  const { t, locale } = useI18n();
  const [query, setQuery] = useState("");
  const [selectedTopK, setSelectedTopK] = useState(settings.top_k_default);
  const [showCustom, setShowCustom] = useState(false);
  const [customValue, setCustomValue] = useState("");
  const [copiedIndex, setCopiedIndex] = useState(null);
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
  }, [messages]);

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

  const send = async (e) => {
    e.preventDefault();
    const q = query.trim();
    if (!q || pending) return;
    const uploadHint = resolveUploadHint(effectiveTags);
    setMessages((m) => [...m, { role: "user", text: q, query: q }]);
    setQuery("");
    setPending(true);
    setMessages((m) => [...m, { role: "assistant", text: t("chat.thinking"), sources: [] }]);
    try {
      const resp = await chat(q, effectiveTags, selectedTopK, selectedMode, sessionId, sourceLocale, useGlossary);
      if (resp.session_id) setSessionId(resp.session_id);
      setMessages((m) => {
        const copy = [...m];
        copy[copy.length - 1] = { role: "assistant", text: resp.answer, sources: resp.sources, uploadHint, applied_terms: resp.applied_terms, expansion_status: resp.expansion_status, query: q, requestTags: effectiveTags, requestTopK: selectedTopK, requestMode: selectedMode, requestSourceLocale: sourceLocale };
        return copy;
      });
    } catch (err) {
      if (err.status === 409) {
        // Сессия была удалена (в корзине): сбрасываем тред и поле ввода без
        // авто-повтора — пользователь сам решает, повторять ли вопрос в новом чате.
        startNewChat();
        setQuery("");
        showToast(t("chat.sessionDeleted"), { type: "error" });
      } else {
        setMessages((m) => {
          const copy = [...m];
          copy[copy.length - 1] = { role: "assistant", text: t("chat.errorPrefix", { message: friendlyApiError(err, t) }), sources: [], query: q };
          return copy;
        });
      }
    } finally {
      setPending(false);
    }
  };

  const repeatWithoutGlossary = async (message) => {
    if (pending || !message.query) return;
    const q = message.query;
    setMessages((items) => [...items, { role: "user", text: q, query: q }]);
    setPending(true);
    setMessages((items) => [...items, { role: "assistant", text: t("chat.thinking"), sources: [] }]);
    try {
      const resp = await chat(q, message.requestTags ?? effectiveTags, message.requestTopK ?? selectedTopK, message.requestMode ?? selectedMode, sessionId, message.requestSourceLocale ?? sourceLocale, false);
      if (resp.session_id) setSessionId(resp.session_id);
      setMessages((items) => {
        const copy = [...items];
        copy[copy.length - 1] = { role: "assistant", text: resp.answer, sources: resp.sources, applied_terms: resp.applied_terms, expansion_status: resp.expansion_status, query: q };
        return copy;
      });
    } catch (err) {
      setMessages((items) => {
        const copy = [...items];
        copy[copy.length - 1] = { role: "assistant", text: t("chat.errorPrefix", { message: friendlyApiError(err, t) }), sources: [], query: q };
        return copy;
      });
    } finally { setPending(false); }
  };

  return (
    <section className="panel">
      <div className="chat-toolbar">
        <Link href="/chat/history" className="btn ghost">
          {t("chat.historyBtn")}
        </Link>
        <button
          type="button"
          className="btn ghost"
          onClick={startNewChat}
          disabled={pending || messages.length === 0}
          title={t("chat.newChatTitle")}
        >
          {t("chat.newChat")}
        </button>
      </div>
      <div className="chat-log" ref={logRef}>
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
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
            {m.role === "assistant" && <AppliedTerms status={m.expansion_status} appliedTerms={m.applied_terms} />}
            {m.role === "assistant" && m.applied_terms?.length > 0 && (
              <button type="button" className="btn ghost glossary-repeat" onClick={() => repeatWithoutGlossary(m)} disabled={pending}>
                {t("chat.glossary.repeatWithout")}
              </button>
            )}
            {m.sources && m.sources.length > 0 && (
              <details className="sources">
                <summary>{t("chat.sources")}</summary>
                <ol>
                  {m.sources.map((s, j) => {
                    const href = sourceHref(s);
                    const isChunk = s.point_type === "chunk";
                    const badge = isChunk ? "\u{1F4E6}" : "\u{1F4C4}";
                    return (
                      <li key={j}>
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
        ))}
      </div>
      <details className="search-settings">
        <summary>{t("chat.searchSettings")}</summary>
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
      <div className="topk-picker" role="radiogroup" aria-label={t("chat.topkLabel")}>
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
      </div>
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
        <select
          className="doc-filter-select"
          value={sourceLocale}
          onChange={(e) => setSourceLocale(e.target.value)}
          aria-label={t("docs.localeFilterAria")}
        >
          <option value="">{t("docs.allLocales")}</option>
          {facetOptions(localeFacets, locale).map((o) => (
            <option key={o.code} value={o.code}>
              {o.code === "unknown" ? t("docs.localeUnknown") : o.label} ({o.count})
            </option>
          ))}
        </select>
      </div>
      <form className="chat-form" onSubmit={send}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("chat.queryPlaceholder")}
          autoComplete="off"
        />
        <button type="submit" disabled={pending}>
          {t("chat.send")}
        </button>
      </form>
    </section>
  );
}
