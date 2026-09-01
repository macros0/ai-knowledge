"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { blockUser, listAudit, listAuditActionTypes, listAuditUsers, listBlocks, unblockUser } from "@/lib/api";
import { useToast } from "./Toast";

const ACTION_LABELS = {
  document_upload: "Загрузка документа",
  document_delete: "Удаление документа",
  document_bulk_delete: "Массовое удаление",
  document_regenerate: "Перегенерация документа",
  document_bulk_regenerate: "Массовая перегенерация",
  document_resume: "Возобновление документа",
  document_development_set: "Привязка разработки",
  document_tags_update: "Правка тегов документа",
  document_bulk_tags_update: "Массовая правка тегов",
  job_approve: "Одобрение задачи",
  job_cancel: "Отмена задачи",
  user_block: "Блокировка пользователя",
  user_unblock: "Разблокировка пользователя",
  development_create: "Создание разработки",
  development_update: "Обновление разработки",
  development_delete: "Удаление разработки",
  attribute_create: "Создание значения атрибута",
  attribute_delete: "Удаление значения атрибута",
  tag_delete: "Удаление тега из справочника",
  tag_cleanup: "Очистка неиспользуемых тегов",
  document_restore: "Восстановление документа",
  document_bulk_restore: "Массовое восстановление",
  document_auto_delete: "Автоудаление документа (система)",
  chat_history_view: "Просмотр чужой истории чата",
  chat_history_auto_delete: "Автоочистка истории чата (система)",
};

const ACTION_OPTIONS = Object.keys(ACTION_LABELS);

function fmtTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

// Защита от CSV-инъекции: значения, начинающиеся с = + - @, Excel трактует как
// формулы (username/reason — пользовательский ввод). Префикс "'" нейтрализует.
function csvCell(v) {
  let s = String(v ?? "");
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
  return `"${s.replace(/"/g, '""')}"`;
}

function exportCsv(entries) {
  const header = ["time", "user_id", "username", "action_type", "target_type", "target_id", "ip_address"];
  const rows = [header.join(",")];
  for (const e of entries) {
    rows.push(
      [
        e.created_at,
        e.user_id ?? "",
        e.username ?? "",
        e.action_type,
        e.target_type ?? "",
        e.target_id ?? "",
        e.ip_address ?? "",
      ]
        .map(csvCell)
        .join(",")
    );
  }
  const blob = new Blob(["\uFEFF" + rows.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `audit-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function aggregate(entries) {
  const map = {};
  for (const e of entries) {
    const key = `${e.username ?? "?"} · ${ACTION_LABELS[e.action_type] ?? e.action_type}`;
    map[key] = (map[key] || 0) + 1;
  }
  return Object.entries(map)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 15);
}

export default function SecurityPanel() {
  const { showToast } = useToast();
  const [entries, setEntries] = useState([]);
  const [blocks, setBlocks] = useState([]);
  const [actionTypes, setActionTypes] = useState([]);
  const [users, setUsers] = useState([]);
  const [filters, setFilters] = useState({});
  const [draft, setDraft] = useState({ user_id: "", action_type: "", since: "", until: "" });
  const [blockForm, setBlockForm] = useState({ external_id: "", reason: "", expires_at: "" });
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);

  const load = useCallback(async () => {
    try {
      const [ents, blk] = await Promise.all([listAudit({ ...filters, limit: 200 }), listBlocks()]);
      if (mounted.current) {
        setEntries(ents);
        setBlocks(blk);
      }
    } catch (err) {
      if (mounted.current) showToast(err.message, { type: "error" });
    }
  }, [filters, showToast]);

  useEffect(() => {
    mounted.current = true;
    load();
    return () => {
      mounted.current = false;
    };
  }, [load]);

  // Справочники для фильтров: полный перечень действий и пользователи журнала.
  useEffect(() => {
    listAuditActionTypes().then(setActionTypes).catch(() => {});
    listAuditUsers().then(setUsers).catch(() => {});
  }, []);

  const applyFilters = (e) => {
    e.preventDefault();
    const next = {};
    for (const [k, v] of Object.entries(draft)) {
      if (!v) continue;
      if (k === "since") next[k] = `${v}T00:00:00`;
      else if (k === "until") next[k] = `${v}T23:59:59`;
      else next[k] = v;
    }
    setFilters(next);
  };

  const submitBlock = async (e) => {
    e.preventDefault();
    if (!blockForm.external_id.trim()) return;
    setBusy(true);
    try {
      await blockUser(blockForm.external_id.trim(), {
        reason: blockForm.reason || null,
        expires_at: blockForm.expires_at || null,
      });
      showToast("Пользователь заблокирован", { type: "success" });
      setBlockForm({ external_id: "", reason: "", expires_at: "" });
      await load();
    } catch (err) {
      showToast(err.message, { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  const submitUnblock = async (externalId) => {
    try {
      await unblockUser(externalId);
      showToast("Пользователь разблокирован", { type: "success" });
      await load();
    } catch (err) {
      showToast(err.message, { type: "error" });
    }
  };

  const anomalies = aggregate(entries);

  return (
    <section className="panel security-panel">
      <div className="panel-head">
        <h2>Аудит и безопасность</h2>
        <button className="modal-btn" onClick={() => exportCsv(entries)} disabled={entries.length === 0}>
          Экспорт CSV
        </button>
      </div>

      <div className="sec-grid">
        <div className="sec-col">
          <h3>Журнал ИБ</h3>
          <form className="audit-filters" onSubmit={applyFilters}>
            <select
              className="filter-input"
              value={draft.user_id}
              onChange={(e) => setDraft((d) => ({ ...d, user_id: e.target.value }))}
            >
              <option value="">Все пользователи</option>
              {users.map((u) => (
                <option key={u.user_id} value={u.user_id}>
                  {u.username ?? u.user_id}
                </option>
              ))}
            </select>
            <select
              className="filter-input"
              value={draft.action_type}
              onChange={(e) => setDraft((d) => ({ ...d, action_type: e.target.value }))}
            >
              <option value="">Все действия</option>
              {(actionTypes.length > 0 ? actionTypes : ACTION_OPTIONS).map((a) => (
                <option key={a} value={a}>
                  {ACTION_LABELS[a] ?? a}
                </option>
              ))}
            </select>
            <input
              className="filter-input"
              type="date"
              value={draft.since}
              onChange={(e) => setDraft((d) => ({ ...d, since: e.target.value }))}
            />
            <input
              className="filter-input"
              type="date"
              value={draft.until}
              onChange={(e) => setDraft((d) => ({ ...d, until: e.target.value }))}
            />
            <button className="modal-btn" type="submit">
              Применить
            </button>
          </form>

          {entries.length === 0 ? (
            <p className="muted">Записей не найдено.</p>
          ) : (
            <div className="audit-table-wrap">
              <table className="audit-table">
                <thead>
                  <tr>
                    <th>Время</th>
                    <th>Пользователь</th>
                    <th>Действие</th>
                    <th>Объект</th>
                    <th>IP</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((e) => (
                    <tr key={e.id}>
                      <td className="nowrap">{fmtTime(e.created_at)}</td>
                      <td>{e.username ?? e.user_id ?? "—"}</td>
                      <td>{ACTION_LABELS[e.action_type] ?? e.action_type}</td>
                      <td>
                        {e.target_type ? `${e.target_type}:` : ""}
                        {e.target_id ?? ""}
                      </td>
                      <td>{e.ip_address ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="sec-col">
          <h3>Аномалии (по окну)</h3>
          {anomalies.length === 0 ? (
            <p className="muted">Нет данных для агрегации.</p>
          ) : (
            <ul className="anomaly-list">
              {anomalies.map(([label, count]) => (
                <li key={label} className="anomaly-item">
                  <span>{label}</span>
                  <span className="anomaly-count">{count}</span>
                </li>
              ))}
            </ul>
          )}

          <h3>Блокировки</h3>
          <form className="block-form" onSubmit={submitBlock}>
            <input
              className="filter-input"
              placeholder="Внешний id пользователя"
              value={blockForm.external_id}
              onChange={(e) => setBlockForm((b) => ({ ...b, external_id: e.target.value }))}
            />
            <input
              className="filter-input"
              placeholder="Причина"
              value={blockForm.reason}
              onChange={(e) => setBlockForm((b) => ({ ...b, reason: e.target.value }))}
            />
            <input
              className="filter-input"
              type="datetime-local"
              value={blockForm.expires_at}
              onChange={(e) => setBlockForm((b) => ({ ...b, expires_at: e.target.value }))}
            />
            <button className="modal-btn danger" type="submit" disabled={busy}>
              Заблокировать
            </button>
          </form>

          {blocks.length === 0 ? (
            <p className="muted">Активных блокировок нет.</p>
          ) : (
            <ul className="block-list">
              {blocks.map((b) => (
                <li key={b.id} className="block-item">
                  <div>
                    <strong>{b.username ?? b.external_id}</strong>
                    {!b.active && <span className="block-expired"> (истекла)</span>}
                    <div className="muted">
                      {b.reason ? `Причина: ${b.reason} · ` : ""}
                      {b.blocked_by ? `кем: ${b.blocked_by} · ` : ""}
                      до {b.expires_at ? fmtTime(b.expires_at) : "бессрочно"}
                    </div>
                  </div>
                  <button className="modal-btn" onClick={() => submitUnblock(b.external_id)}>
                    Разблокировать
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}
