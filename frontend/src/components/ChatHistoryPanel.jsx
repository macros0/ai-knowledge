"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { deleteChatSession, getChatThread, listChatSessions } from "@/lib/api";
import { fmtDate, HistoryMessage } from "./ChatHistoryShared";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";
import Modal from "./Modal";

export default function ChatHistoryPanel() {
  const { showToast } = useToast();
  const { t, tc } = useI18n();
  const [sessions, setSessions] = useState([]);
  const [active, setActive] = useState(null);
  const [busy, setBusy] = useState(false);
  const [pendingDelete, setPendingDelete] = useState(null);

  const load = useCallback(async () => {
    const result = await listChatSessions();
    setSessions(result.sessions);
  }, []);

  useEffect(() => {
    load().catch((err) => showToast(t("chat.historyLoadError", { message: err.message }), { type: "error" }));
  }, [load, showToast, t]);

  const openThread = async (sid) => {
    setBusy(true);
    try {
      setActive(await getChatThread(sid));
    } catch (err) {
      showToast(t("chat.openThreadError", { message: err.message }), { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  const confirmDelete = async () => {
    const sid = pendingDelete?.session_id;
    if (!sid) return;
    try {
      await deleteChatSession(sid);
      showToast(t("chat.threadDeleted"), { type: "success" });
      setPendingDelete(null);
      if (active?.session_id === sid) setActive(null);
      load();
    } catch (err) {
      showToast(t("chat.deleteError", { message: err.message }), { type: "error" });
    }
  };

  return (
    <section className="panel">
      <h2 className="panel-title">{t("chat.historyTitle")}</h2>

      {active ? (
        <div className="history-thread">
          <div className="history-thread-head">
            <button className="btn ghost" onClick={() => setActive(null)}>
              {t("chat.backToList")}
            </button>
            <div className="history-thread-title">
              <strong>{active.title || t("chat.untitled")}</strong>
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
          {t("chat.historyEmpty")}{" "}
          <Link href="/chat" className="history-link">
            {t("chat.askFirst")}
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
                <span className="history-item-title">{s.title || t("chat.untitled")}</span>
                <span className="meta">
                  {tc("chat.messagesCount", s.message_count)} · {fmtDate(s.updated_at)}
                </span>
              </button>
              <button
                className="btn ghost"
                onClick={() => setPendingDelete(s)}
                title={t("chat.deleteThreadTitle")}
              >
                {t("chat.deleteThread")}
              </button>
            </li>
          ))}
        </ul>
      )}

      {pendingDelete && (
        <Modal
          title={t("chat.deleteThreadConfirmTitle")}
          onClose={() => setPendingDelete(null)}
          footer={
            <>
              <button className="modal-btn" onClick={() => setPendingDelete(null)}>
                {t("common.cancel")}
              </button>
              <button className="modal-btn danger" onClick={confirmDelete}>
                {t("chat.deleteThread")}
              </button>
            </>
          }
        >
          <p className="confirm-text">
            {t("chat.deleteThreadConfirmText", { title: pendingDelete.title || t("chat.untitled") })}
          </p>
        </Modal>
      )}
    </section>
  );
}
