"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { getAdminChatThread, listAdminChatSessions, listAdminChatUsers } from "@/lib/api";
import { fmtDate, HistoryMessage } from "./ChatHistoryShared";
import { useToast } from "./Toast";

export default function AdminChatHistoryPanel() {
  const { showToast } = useToast();
  const [users, setUsers] = useState([]);
  const [query, setQuery] = useState("");
  const [selectedUser, setSelectedUser] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [active, setActive] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    listAdminChatUsers()
      .then(setUsers)
      .catch((err) => showToast(`Не удалось загрузить пользователей: ${err.message}`, { type: "error" }));
  }, [showToast]);

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
        showToast(`Не удалось загрузить сессии: ${err.message}`, { type: "error" });
      } finally {
        setBusy(false);
      }
    },
    [showToast]
  );

  const openThread = async (sid) => {
    setBusy(true);
    try {
      setActive(await getAdminChatThread(selectedUser.user_id, sid));
    } catch (err) {
      showToast(`Не удалось открыть тред: ${err.message}`, { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel">
      <h2 className="panel-title">История чата пользователей</h2>
      <p className="history-admin-hint">
        Просмотр содержимого чужих тредов фиксируется в журнале ИБ (audit).
      </p>

      <div className="history-admin-search">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Поиск пользователя (ID или логин)..."
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
                  {u.user_id} · {u.session_count} тредов
                </span>
              </button>
            </li>
          ))}
          {filtered.length === 0 && <li className="history-empty">Ничего не найдено</li>}
        </ul>
      ) : active ? (
        <div className="history-thread">
          <div className="history-thread-head">
            <button className="btn ghost" onClick={() => setActive(null)}>
              ← К сессиям
            </button>
            <div className="history-thread-title">
              <strong>{active.title || "Без названия"}</strong>
              <span className="meta">{fmtDate(active.created_at)}</span>
            </div>
          </div>
          <div className="history-admin-banner">
            Просмотр истории пользователя{" "}
            <strong>{selectedUser.username || selectedUser.user_id}</strong> ({selectedUser.user_id}).
            Действие логируется.
          </div>
          <div className="chat-log history-log">
            {active.messages.map((m, i) => (
              <HistoryMessage key={i} m={m} userLabel="Пользователь" />
            ))}
          </div>
        </div>
      ) : (
        <div className="history-thread">
          <div className="history-thread-head">
            <button className="btn ghost" onClick={() => setSelectedUser(null)}>
              ← К пользователям
            </button>
            <div className="history-thread-title">
              <strong>{selectedUser.username || selectedUser.user_id}</strong>
              <span className="meta">{selectedUser.user_id}</span>
            </div>
          </div>
          {busy ? (
            <p className="history-empty">Загрузка...</p>
          ) : sessions.length === 0 ? (
            <p className="history-empty">У пользователя нет истории</p>
          ) : (
            <ul className="history-list">
              {sessions.map((s) => (
                <li key={s.session_id} className="history-item">
                  <button className="history-open" onClick={() => openThread(s.session_id)}>
                    <span className="history-item-title">{s.title || "Без названия"}</span>
                    <span className="meta">
                      {s.message_count} сообщ. · {fmtDate(s.updated_at)}
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
