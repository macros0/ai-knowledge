"use client";

import { useEffect, useRef, useState } from "react";
import { chat } from "@/lib/api";
import TagPicker from "./TagPicker";
import { useChat } from "@/context/ChatContext";

export default function ChatPanel() {
  const { messages, tags, pending, setMessages, setTags, setPending } = useChat();
  const [query, setQuery] = useState("");
  const logRef = useRef(null);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [messages]);

  const send = async (e) => {
    e.preventDefault();
    const q = query.trim();
    if (!q || pending) return;
    setMessages((m) => [...m, { role: "user", text: q }]);
    setQuery("");
    setPending(true);
    setMessages((m) => [...m, { role: "assistant", text: "Думаю...", sources: [] }]);
    try {
      const resp = await chat(q, tags);
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
            <div className="bubble">{m.text}</div>
            {m.sources && m.sources.length > 0 && (
              <details className="sources">
                <summary>Источники</summary>
                <ol>
                  {m.sources.map((s, j) => (
                    <li key={j}>
                      {s.title} (релевантность {(s.score * 100).toFixed(0)}%)
                    </li>
                  ))}
                </ol>
              </details>
            )}
          </div>
        ))}
      </div>
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
