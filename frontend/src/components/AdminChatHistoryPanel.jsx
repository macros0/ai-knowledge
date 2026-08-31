"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { getAdminChatThread, listAdminChatSessions, listAdminChatUsers } from "@/lib/api";
import { CiteLink, remarkCiteLinks, sourceHref } from "@/lib/chatSources";
import MarkdownViewer from "./MarkdownViewer";
import { useToast } from "./Toast";

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
      <div className="role">{m.role === "user" ? "Пользователь" : "Ассистент"}</div>
      <div className="bubble history-bubble">{content}</div>
      <SourceBadges sources={m.sources} />
    </div>
  );
}

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
              <HistoryMessage key={i} m={m} />
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
