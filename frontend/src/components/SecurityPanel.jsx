"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { blockUser, friendlyApiError, listAudit, listAuditActionTypes, listAuditUsers, listBlocks, unblockUser } from "@/lib/api";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";

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

export default function SecurityPanel() {
  const { showToast } = useToast();
  const { t, fmtDateTime } = useI18n();
  const [entries, setEntries] = useState([]);
  const [blocks, setBlocks] = useState([]);
  const [actionTypes, setActionTypes] = useState([]);
  const [users, setUsers] = useState([]);
  const [filters, setFilters] = useState({});
  const [draft, setDraft] = useState({ user_id: "", action_type: "", since: "", until: "" });
  const [blockForm, setBlockForm] = useState({ external_id: "", reason: "", expires_at: "" });
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);

  const KNOWN_ACTIONS = [
    "document_upload", "document_delete", "document_bulk_delete", "document_regenerate",
    "document_bulk_regenerate", "document_resume", "document_development_set",
    "document_tags_update", "document_bulk_tags_update", "job_approve", "job_cancel",
    "user_block", "user_unblock", "development_create", "development_update",
    "development_delete", "attribute_create", "attribute_delete", "tag_delete", "tag_cleanup",
    "document_restore", "document_bulk_restore", "document_auto_delete",
    "chat_history_view", "chat_history_auto_delete",
  ];

  const actionLabels = (type) => {
    const key = `security.action.${type}`;
    const translated = t(key);
    return translated === key ? type : translated;
  };

  const load = useCallback(async () => {
    try {
      const [ents, blk] = await Promise.all([listAudit({ ...filters, limit: 200 }), listBlocks()]);
      if (mounted.current) {
        setEntries(ents);
        setBlocks(blk);
      }
    } catch (err) {
      if (mounted.current) showToast(friendlyApiError(err, t), { type: "error" });
    }
    // t в зависимостях: текст ошибки берётся по коду из словаря, и после
    // переключения языка мемоизированный load не должен держать старый t.
  }, [filters, showToast, t]);

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
      showToast(t("security.userBlocked"), { type: "success" });
      setBlockForm({ external_id: "", reason: "", expires_at: "" });
      await load();
    } catch (err) {
      showToast(friendlyApiError(err, t), { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  const submitUnblock = async (externalId) => {
    try {
      await unblockUser(externalId);
      showToast(t("security.userUnblocked"), { type: "success" });
      await load();
    } catch (err) {
      showToast(friendlyApiError(err, t), { type: "error" });
    }
  };

  // Агрегация аномалий по окну.
  const anomalies = useCallback(() => {
    const map = {};
    for (const e of entries) {
      const key = `${e.username ?? "?"} · ${actionLabels(e.action_type)}`;
      map[key] = (map[key] || 0) + 1;
    }
    return Object.entries(map)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 15);
  }, [entries, t]);

  const agg = anomalies();

  return (
    <section className="panel security-panel">
      <div className="panel-head">
        <h2>{t("security.title")}</h2>
        <button className="modal-btn" onClick={() => exportCsv(entries)} disabled={entries.length === 0}>
          {t("security.exportCsv")}
        </button>
      </div>

      <div className="sec-grid">
        <div className="sec-col">
          <h3>{t("security.log")}</h3>
          <form className="audit-filters" onSubmit={applyFilters}>
            <select
              className="filter-input"
              value={draft.user_id}
              onChange={(e) => setDraft((d) => ({ ...d, user_id: e.target.value }))}
            >
              <option value="">{t("security.allUsers")}</option>
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
              <option value="">{t("security.allActions")}</option>
              {(actionTypes.length > 0 ? actionTypes : KNOWN_ACTIONS).map((a) => (
                <option key={a} value={a}>
                  {actionLabels(a)}
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
              {t("security.apply")}
            </button>
          </form>

          {entries.length === 0 ? (
            <p className="muted">{t("security.noRecords")}</p>
          ) : (
            <div className="audit-table-wrap">
              <table className="audit-table">
                <thead>
                  <tr>
                    <th>{t("security.colTime")}</th>
                    <th>{t("security.colUser")}</th>
                    <th>{t("security.colAction")}</th>
                    <th>{t("security.colTarget")}</th>
                    <th>{t("security.colIp")}</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((e) => (
                    <tr key={e.id}>
                      <td className="nowrap">{fmtDateTime(e.created_at)}</td>
                      <td>{e.username ?? e.user_id ?? "—"}</td>
                      <td>{actionLabels(e.action_type)}</td>
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
          <h3>{t("security.anomalies")}</h3>
          {agg.length === 0 ? (
            <p className="muted">{t("security.noAnomalies")}</p>
          ) : (
            <ul className="anomaly-list">
              {agg.map(([label, count]) => (
                <li key={label} className="anomaly-item">
                  <span>{label}</span>
                  <span className="anomaly-count">{count}</span>
                </li>
              ))}
            </ul>
          )}

          <h3>{t("security.blocks")}</h3>
          <form className="block-form" onSubmit={submitBlock}>
            <input
              className="filter-input"
              placeholder={t("security.blockExternalId")}
              value={blockForm.external_id}
              onChange={(e) => setBlockForm((b) => ({ ...b, external_id: e.target.value }))}
            />
            <input
              className="filter-input"
              placeholder={t("security.blockReason")}
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
              {t("security.blockBtn")}
            </button>
          </form>

          {blocks.length === 0 ? (
            <p className="muted">{t("security.noBlocks")}</p>
          ) : (
            <ul className="block-list">
              {blocks.map((b) => (
                <li key={b.id} className="block-item">
                  <div>
                    <strong>{b.username ?? b.external_id}</strong>
                    {!b.active && <span className="block-expired">{t("security.blockExpired")}</span>}
                    <div className="muted">
                      {b.reason ? t("security.reason", { reason: b.reason }) : ""}
                      {b.blocked_by ? t("security.by", { name: b.blocked_by }) : ""}
                      {t("security.until", { time: b.expires_at ? fmtDateTime(b.expires_at) : t("security.untilForever") })}
                    </div>
                  </div>
                  <button className="modal-btn" onClick={() => submitUnblock(b.external_id)}>
                    {t("security.unblock")}
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
