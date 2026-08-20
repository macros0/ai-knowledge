"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { chat } from "@/lib/api";
import TagPicker from "./TagPicker";
import MarkdownViewer from "./MarkdownViewer";
import { CheckIcon, CopyIcon } from "./icons";
import { useChat } from "@/context/ChatContext";

function getPresetLabel(preset, settings) {
  if (preset === settings.top_k_default) return "Стандартно";
  const minPreset = Math.min(...settings.top_k_presets);
  const maxPreset = Math.max(...settings.top_k_presets);
  if (preset === minPreset) return "Кратко";
  if (preset === maxPreset) return "Подробно";
  return String(preset);
}

function remarkCiteLinks() {
  function splitCites(node) {
    const parts = node.value.split(/(\[\d+\])/g);
    if (parts.length === 1) return [node];
    const out = [];
    for (const part of parts) {
      const m = /^\[(\d+)\]$/.exec(part);
      if (!m) {
        if (part) out.push({ type: node.type, value: part });
        continue;
      }
      const n = Number(m[1]);
      out.push({
        type: "link",
        url: `#cite-${n}`,
        children: [{ type: "text", value: `[${n}]` }],
      });
    }
    return out;
  }

  function transformChildren(parent) {
    if (!parent || !Array.isArray(parent.children)) return;
    const next = [];
    for (const child of parent.children) {
      if ((child.type === "text" || child.type === "inlineCode") && child.value) {
        const converted = splitCites(child);
        if (converted.length === 1 && converted[0] === child) {
          next.push(child);
        } else {
          next.push(...converted);
        }
      } else {
        next.push(child);
      }
      transformChildren(child);
    }
    parent.children = next;
  }

  return (tree) => {
    transformChildren(tree);
  };
}

function sourceHref(s) {
  if (!s || !s.doc_id) return null;
  if (s.point_type === "chunk" && s.chunk_index != null)
    return `/documents/${s.doc_id}/chunks/${s.chunk_index}`;
  if (s.filename)
    return `/documents/${s.doc_id}/okf/${encodeURIComponent(s.filename)}`;
  return `/documents/${s.doc_id}/okf`;
}

function CiteLink({ href, children, sources, ...props }) {
  const m = /^#cite-(\d+)$/.exec(href || "");
  if (!m) return <a href={href} {...props}>{children}</a>;
  const n = Number(m[1]);
  const s = sources[n - 1];
  const url = sourceHref(s);
  if (!url) {
    return <span className="cite">{children}</span>;
  }
  return (
    <Link className="cite" href={url} title={s.title}>
      {children}
    </Link>
  );
}

export default function ChatPanel() {
  const { messages, tags, pending, settings, selectedMode, setMessages, setTags, setPending, setSelectedMode, MODE_LABELS } = useChat();
  const [query, setQuery] = useState("");
  const [selectedTopK, setSelectedTopK] = useState(settings.top_k_default);
  const [showCustom, setShowCustom] = useState(false);
  const [customValue, setCustomValue] = useState("");
  const [copiedIndex, setCopiedIndex] = useState(null);
  const logRef = useRef(null);
  const copyTimerRef = useRef(null);

  useEffect(() => {
    return () => {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    };
  }, []);

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

  const send = async (e) => {
    e.preventDefault();
    const q = query.trim();
    if (!q || pending) return;
    setMessages((m) => [...m, { role: "user", text: q }]);
    setQuery("");
    setPending(true);
    setMessages((m) => [...m, { role: "assistant", text: "Думаю...", sources: [] }]);
    try {
      const resp = await chat(q, tags, selectedTopK, selectedMode);
      setMessages((m) => {
        const copy = [...m];
        copy[copy.length - 1] = { role: "assistant", text: resp.answer, sources: resp.sources };
        return copy;
      });
    } catch (err) {
      setMessages((m) => {
        const copy = [...m];
        copy[copy.length - 1] = { role: "assistant", text: `Ошибка: ${err.message}`, sources: [] };
        return copy;
      });
    } finally {
      setPending(false);
    }
  };

  return (
    <section className="panel">
      <div className="chat-log" ref={logRef}>
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div className="role-row">
              <div className="role">{m.role === "user" ? "Вы" : "Ассистент"}</div>
              {m.role === "assistant" && (
                <button
                  type="button"
                  className="copy-btn"
                  disabled={pending && i === messages.length - 1}
                  onClick={() => copyAnswer(i, m.text)}
                  title="Скопировать ответ в Markdown"
                >
                  {copiedIndex === i ? <CheckIcon size={14} /> : <CopyIcon size={14} />}
                  {copiedIndex === i ? "Скопировано" : "Копировать"}
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
            {m.sources && m.sources.length > 0 && (
              <details className="sources">
                <summary>Источники</summary>
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
                        (релевантность {(s.score * 100).toFixed(0)}%)
                        {s.snippet && <div className="source-snippet">{s.snippet}</div>}
                      </li>
                    );
                  })}
                </ol>
              </details>
            )}
          </div>
        ))}
      </div>
      <details className="search-settings">
        <summary>Параметры поиска</summary>
        <div className="mode-picker" role="radiogroup" aria-label="Режим поиска">
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
      <div className="topk-picker" role="radiogroup" aria-label="Число результатов для ответа">
        <span className="topk-label">Результатов:</span>
        {settings.top_k_presets.map((preset) => (
          <button
            key={preset}
            type="button"
            role="radio"
            aria-checked={selectedTopK === preset}
            className={`topk-btn ${selectedTopK === preset ? "active" : ""}`}
            onClick={() => selectPreset(preset)}
          >
            {preset} — {getPresetLabel(preset, settings)}
          </button>
        ))}
        <button
          type="button"
          className="topk-btn"
          aria-pressed={showCustom}
          onClick={() => setShowCustom((v) => !v)}
        >
          Другое
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
        label="Фильтр по тегам:"
        placeholder="Выберите из справочника или напишите новый..."
        selected={tags}
        onChange={setTags}
      />
      <form className="chat-form" onSubmit={send}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Задайте вопрос по базе знаний..."
          autoComplete="off"
        />
        <button type="submit" disabled={pending}>
          Отправить
        </button>
      </form>
    </section>
  );
}