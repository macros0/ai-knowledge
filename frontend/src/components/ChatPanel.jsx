"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { chat } from "@/lib/api";
import TagPicker from "./TagPicker";
import { useChat } from "@/context/ChatContext";

function getPresetLabel(preset, settings) {
  if (preset === settings.top_k_default) return "Стандартно";
  const minPreset = Math.min(...settings.top_k_presets);
  const maxPreset = Math.max(...settings.top_k_presets);
  if (preset === minPreset) return "Кратко";
  if (preset === maxPreset) return "Подробно";
  return String(preset);
}

function renderAnswer(text, sources) {
  if (!sources || sources.length === 0) return text;
  const parts = text.split(/(\[\d+\])/g);
  return parts.map((part, idx) => {
    const m = /^\[(\d+)\]$/.exec(part);
    if (!m) return part;
    const n = Number(m[1]);
    if (n < 1 || n > sources.length) return part;
    const s = sources[n - 1];
    if (!s.doc_id || !s.filename) {
      return (
        <span key={idx} className="cite">
          {part}
        </span>
      );
    }
    return (
      <Link
        key={idx}
        className="cite"
        href={`/documents/${s.doc_id}/okf/${encodeURIComponent(s.filename)}`}
        title={s.title}
      >
        {part}
      </Link>
    );
  });
}

export default function ChatPanel() {
  const { messages, tags, pending, settings, selectedMode, setMessages, setTags, setPending, setSelectedMode, MODE_LABELS } = useChat();
  const [query, setQuery] = useState("");
  const [selectedTopK, setSelectedTopK] = useState(settings.top_k_default);
  const [showCustom, setShowCustom] = useState(false);
  const [customValue, setCustomValue] = useState("");
  const logRef = useRef(null);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [messages]);

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
            <div className="role">{m.role === "user" ? "Вы" : "Ассистент"}</div>
            <div className="bubble">
              {m.role === "assistant" && m.sources && m.sources.length > 0
                ? renderAnswer(m.text, m.sources)
                : m.text}
            </div>
            {m.sources && m.sources.length > 0 && (
              <details className="sources">
                <summary>Источники</summary>
                <ol>
                  {m.sources.map((s, j) => (
                    <li key={j}>
                      {s.doc_id && s.filename ? (
                        <>
                          <Link
                            className="source-link"
                            href={`/documents/${s.doc_id}/okf/${encodeURIComponent(s.filename)}`}
                          >
                            {s.title}
                          </Link>{" "}
                        </>
                      ) : (
                        s.title
                      )}
                      (релевантность {(s.score * 100).toFixed(0)}%)
                      {s.snippet && <div className="source-snippet">{s.snippet}</div>}
                    </li>
                  ))}
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
      <div className="topk-picker" role="radiogroup" aria-label="Число концептов для ответа">
        <span className="topk-label">Концептов:</span>
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