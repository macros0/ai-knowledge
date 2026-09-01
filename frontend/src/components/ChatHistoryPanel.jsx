"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { deleteChatSession, getChatThread, listChatSessions } from "@/lib/api";
import { fmtDate, HistoryMessage } from "./ChatHistoryShared";
import { useToast } from "./Toast";
import Modal from "./Modal";

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
