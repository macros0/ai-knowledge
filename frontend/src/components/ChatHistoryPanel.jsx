"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { deleteChatSession, getChatThread, listChatSessions } from "@/lib/api";
import { CiteLink, remarkCiteLinks, sourceHref } from "@/lib/chatSources";
import MarkdownViewer from "./MarkdownViewer";
import { useToast } from "./Toast";
import Modal from "./Modal";

const fmtDate = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("ru-RU");
};

function SourceBadges({ sources }) {
  if (!sources || sources.length === 0) return null;
  return (
    <div className="history-sources">
      {sources.map((s, i) => {
        const href = sourceHref(s);
        const label = (
          <>
            {s.title || s.filename}
            {s.development_number && (
              <span className="source-dev-badge" title={s.development_name || "Разработка"}>
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

function HistoryMessage({ m }) {
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
      <div className="role">{m.role === "user" ? "Вы" : "Ассистент"}</div>
      <div className="bubble history-bubble">{content}</div>
      <SourceBadges sources={m.sources} />
    </div>
  );
}

export default function ChatHistoryPanel() {
  const { showToast } = useToast();
  const [sessions, setSessions] = useState([]);
  const [active, setActive] = useState(null);
  const [busy, setBusy] = useState(false);
  const [pendingDelete, setPendingDelete] = useState(null);

  const load = useCallback(async () => {
    const result = await listChatSessions();
    setSessions(result.sessions);
  }, []);

  useEffect(() => {
    load().catch((err) => showToast(`Не удалось загрузить историю: ${err.message}`, { type: "error" }));
  }, [load, showToast]);

  const openThread = async (sid) => {
    setBusy(true);
    try {
      setActive(await getChatThread(sid));
    } catch (err) {
      showToast(`Не удалось открыть тред: ${err.message}`, { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  const confirmDelete = async () => {
    const sid = pendingDelete?.session_id;
    if (!sid) return;
    try {
      await deleteChatSession(sid);
      showToast("Тред удалён", { type: "success" });
      setPendingDelete(null);
      if (active?.session_id === sid) setActive(null);
      load();
    } catch (err) {
      showToast(`Не удалось удалить: ${err.message}`, { type: "error" });
    }
  };

  return (
    <section className="panel">
      <h2 className="panel-title">История чата</h2>

      {active ? (
        <div className="history-thread">
          <div className="history-thread-head">
            <button className="btn ghost" onClick={() => setActive(null)}>
              ← К списку
            </button>
            <div className="history-thread-title">
              <strong>{active.title || "Без названия"}</strong>
              <span className="meta">{fmtDate(active.created_at)}</span>
            </div>
          </div>
          <div className="chat-log history-log">
            {active.messages.map((m, i) => (
              <HistoryMessage key={i} m={m} />
            ))}
          </div>
        </div>
      ) : sessions.length === 0 ? (
        <p className="history-empty">
          История пуста.{" "}
          <Link href="/chat" className="history-link">
            Задайте первый вопрос
          </Link>
        </p>
      ) : (
        <ul className="history-list">
          {sessions.map((s) => (
            <li key={s.session_id} className="history-item">
              <button
                className="history-open"
                onClick={() => openThread(s.session_id)}
                disabled={busy}
              >
                <span className="history-item-title">{s.title || "Без названия"}</span>
                <span className="meta">
                  {s.message_count} сообщ. · {fmtDate(s.updated_at)}
                </span>
              </button>
              <button
                className="btn ghost"
                onClick={() => setPendingDelete(s)}
                title="Удалить тред"
              >
                Удалить
              </button>
            </li>
          ))}
        </ul>
      )}

      {pendingDelete && (
        <Modal
          title="Удалить тред?"
          onClose={() => setPendingDelete(null)}
          footer={
            <>
              <button className="modal-btn" onClick={() => setPendingDelete(null)}>
                Отмена
              </button>
              <button className="modal-btn danger" onClick={confirmDelete}>
                Удалить
              </button>
            </>
          }
        >
          <p className="confirm-text">
            Тред «{pendingDelete.title || "Без названия"}» будет удалён. Эта операция необратима.
          </p>
        </Modal>
      )}
    </section>
  );
}
