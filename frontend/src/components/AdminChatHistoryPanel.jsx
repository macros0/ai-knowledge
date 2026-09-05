"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { getAdminChatThread, listAdminChatSessions, listAdminChatUsers } from "@/lib/api";
import { fmtDate, HistoryMessage } from "./ChatHistoryShared";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";

export default function AdminChatHistoryPanel() {
  const { showToast } = useToast();
  const { t, tc } = useI18n();
  const [users, setUsers] = useState([]);
  const [query, setQuery] = useState("");
  const [selectedUser, setSelectedUser] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [active, setActive] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    listAdminChatUsers()
      .then(setUsers)
      .catch((err) => showToast(t("chat.usersLoadError", { message: err.message }), { type: "error" }));
  }, [showToast, t]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return users;
    return users.filter(
      (u) => u.user_id.toLowerCase().includes(q) || (u.username || "").toLowerCase().includes(q)
    );
  }, [users, query]);

  const selectUser = useCallback(
    async (u) => {
      setSelectedUser(u);
      setActive(null);
      setBusy(true);
      try {
        const result = await listAdminChatSessions(u.user_id);
        setSessions(result.sessions);
      } catch (err) {
        showToast(t("chat.sessionsLoadError", { message: err.message }), { type: "error" });
      } finally {
        setBusy(false);
      }
    },
    [showToast, t]
  );

  const openThread = async (sid) => {
    setBusy(true);
    try {
      setActive(await getAdminChatThread(selectedUser.user_id, sid));
    } catch (err) {
      showToast(t("chat.openThreadError", { message: err.message }), { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel">
      <h2 className="panel-title">{t("chat.historyAdminTitle")}</h2>
      <p className="history-admin-hint">{t("chat.adminHint")}</p>

      <div className="history-admin-search">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("chat.userSearchPlaceholder")}
          autoComplete="off"
        />
      </div>

      {!selectedUser ? (
        <ul className="history-list">
          {filtered.map((u) => (
            <li key={u.user_id} className="history-item">
              <button className="history-open" onClick={() => selectUser(u)}>
                <span className="history-item-title">{u.username || u.user_id}</span>
                <span className="meta">
                  {t("chat.userThreads", { user: u.user_id, count: u.session_count })}
                </span>
              </button>
            </li>
          ))}
          {filtered.length === 0 && <li className="history-empty">{t("chat.empty")}</li>}
        </ul>
      ) : active ? (
        <div className="history-thread">
          <div className="history-thread-head">
            <button className="btn ghost" onClick={() => setActive(null)}>
              {t("chat.backToSessions")}
            </button>
            <div className="history-thread-title">
              <strong>{active.title || t("chat.untitled")}</strong>
              <span className="meta">{fmtDate(active.created_at)}</span>
            </div>
          </div>
          <div className="history-admin-banner">
            {t("chat.viewingUser", {
              name: selectedUser.username || selectedUser.user_id,
              id: selectedUser.user_id,
            })}
          </div>
          <div className="chat-log history-log">
            {active.messages.map((m, i) => (
              <HistoryMessage key={i} m={m} userLabel={t("chat.userLabel")} />
            ))}
          </div>
        </div>
      ) : (
        <div className="history-thread">
          <div className="history-thread-head">
            <button className="btn ghost" onClick={() => setSelectedUser(null)}>
              {t("chat.backToUsers")}
            </button>
            <div className="history-thread-title">
              <strong>{selectedUser.username || selectedUser.user_id}</strong>
              <span className="meta">{selectedUser.user_id}</span>
            </div>
          </div>
          {busy ? (
            <p className="history-empty">{t("chat.loading")}</p>
          ) : sessions.length === 0 ? (
            <p className="history-empty">{t("chat.noHistory")}</p>
          ) : (
            <ul className="history-list">
              {sessions.map((s) => (
                <li key={s.session_id} className="history-item">
                  <button className="history-open" onClick={() => openThread(s.session_id)}>
                    <span className="history-item-title">{s.title || t("chat.untitled")}</span>
                    <span className="meta">
                      {tc("chat.messagesCount", s.message_count)} · {fmtDate(s.updated_at)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
