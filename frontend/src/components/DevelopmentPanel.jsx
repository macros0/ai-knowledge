"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  addAttributeValue,
  createDevelopment,
  deleteAttributeValue,
  deleteDevelopment,
  listAttributeValues,
  listDevelopmentsPage,
  updateDevelopment,
} from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { TYPED_CONFIRM_THRESHOLD } from "@/lib/constants";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";
import ConfirmModal from "./ConfirmModal";

const EMPTY_FORM = { number: "", name: "", module: "" };
const PAGE_SIZE = 50;
const MODULE_NONE = "__none__";

export default function DevelopmentPanel() {
  const { mode, hasRole } = useAuth();
  const { showToast } = useToast();
  const { t } = useI18n();
  const [developments, setDevelopments] = useState([]);
  const [total, setTotal] = useState(0);
  const [modules, setModules] = useState([]);
  const [newModule, setNewModule] = useState("");
  const [form, setForm] = useState(EMPTY_FORM);
  const [editing, setEditing] = useState(null); // id редактируемой разработки
  const [editForm, setEditForm] = useState(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [modulesOpen, setModulesOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState(null);

  // Серверные фильтр/сортировка/пагинация.
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [moduleFilter, setModuleFilter] = useState("");
  const [sortBy, setSortBy] = useState("number");
  const [sortOrder, setSortOrder] = useState("asc");
  const [page, setPage] = useState(0);

  const canEdit = mode === "disabled" || hasRole("editor", "admin");
  const canDelete = mode === "disabled" || hasRole("editor", "admin");

  const COLUMNS = [
    { key: "number", label: t("devpanel.colNumber") },
    { key: "name", label: t("devpanel.colName") },
    { key: "module", label: t("devpanel.colModule") },
    { key: "documents_count", label: t("devpanel.colDocs") },
  ];

  const load = useCallback(async () => {
    try {
      const res = await listDevelopmentsPage({
        search: search || undefined,
        module: moduleFilter || undefined,
        sort: sortBy,
        order: sortOrder,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      });
      setDevelopments(res.developments);
      setTotal(res.total);
    } catch (err) {
      showToast(t("devpanel.loadError", { message: err.message }), { type: "error" });
    }
  }, [search, moduleFilter, sortBy, sortOrder, page, showToast, t]);

  const loadModules = useCallback(async () => {
    try {
      const values = await listAttributeValues("module");
      setModules(values.map((v) => v.value));
    } catch {
      setModules([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    loadModules();
  }, [loadModules]);

  // Debounce поиска (не долбим бэкенд на каждый символ).
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const changeModuleFilter = (value) => {
    setModuleFilter(value);
    setPage(0);
  };

  const onSort = (col) => {
    if (sortBy === col) {
      setSortOrder((o) => (o === "asc" ? "desc" : "asc"));
    } else {
      setSortBy(col);
      setSortOrder("asc");
    }
    setPage(0);
  };

  const sortIndicator = (col) => (sortBy === col ? (sortOrder === "asc" ? " ▲" : " ▼") : "");

  // Регистрирует module в справочнике, если его там нет (чтобы бэкенд не ответил 422).
  const ensureModule = useCallback(
    async (value) => {
      const v = (value || "").trim();
      if (!v || modules.includes(v)) return v || null;
      try {
        await addAttributeValue("module", v);
        setModules((prev) => [...prev, v].sort());
      } catch (err) {
        showToast(t("devpanel.addModuleError", { message: err.message }), { type: "error" });
        throw err;
      }
      return v;
    },
    [modules, showToast, t]
  );

  const submitCreate = async (e) => {
    e.preventDefault();
    const number = form.number.trim();
    const name = form.name.trim();
    if (!number || !name) return;
    setBusy(true);
    try {
      const module = await ensureModule(form.module);
      await createDevelopment({ number, name, module });
      setForm(EMPTY_FORM);
      showToast(t("devpanel.created", { name }), { type: "success" });
      await load();
    } catch (err) {
      showToast(t("devpanel.createError", { message: err.message }), { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  const startEdit = (dev) => {
    setEditing(dev.id);
    setEditForm({
      number: dev.number,
      name: dev.name,
      module: dev.module || "",
      originalNumber: dev.number,
      documentsCount: dev.documents_count,
    });
  };

  const submitEdit = async (devId) => {
    const number = editForm.number.trim();
    const name = editForm.name.trim();
    if (!number || !name) return;
    // Контроль целостности при смене номера: если у разработки есть привязанные
    // документы — предупредить, что их проекция в поиске будет обновлена.
    const numberChanged =
      editForm.originalNumber != null && number !== editForm.originalNumber;
    if (numberChanged && (editForm.documentsCount || 0) > 0) {
      if (!window.confirm(t("devpanel.numberChangedConfirm", { count: editForm.documentsCount }))) {
        return;
      }
    }
    setBusy(true);
    try {
      const module = await ensureModule(editForm.module);
      await updateDevelopment(devId, { number, name, module });
      setEditing(null);
      setEditForm(EMPTY_FORM);
      showToast(t("devpanel.saved"), { type: "success" });
      await load();
    } catch (err) {
      if (err.code === "version_conflict") {
        setEditForm((f) => ({ ...f, version: err.data?.current?.version }));
        showToast(t("devpanel.versionConflict"), { type: "error" });
      } else {
        showToast(t("devpanel.saveError", { message: err.message }), { type: "error" });
      }
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async (dev) => {
    setBusy(true);
    try {
      await deleteDevelopment(dev.id, dev.version);
      showToast(t("devpanel.deleted"), { type: "success" });
      setPendingDelete(null);
      await load();
    } catch (err) {
      if (err.code === "version_conflict") {
        showToast(t("devpanel.deleteVersionConflict"), { type: "error" });
        setPendingDelete(null);
        await load();
      } else {
        showToast(t("devpanel.deleteError", { message: err.message }), { type: "error" });
      }
    } finally {
      setBusy(false);
    }
  };

  const remove = (dev) => {
    const count = dev.documents_count || 0;
    if (count > TYPED_CONFIRM_THRESHOLD) {
      setPendingDelete(dev);
      return;
    }
    const hint = count > 0 ? t("devpanel.deleteHint", { count }) : "";
    if (window.confirm(t("devpanel.deleteConfirm", { number: dev.number, name: dev.name, hint }))) {
      doDelete(dev);
    }
  };

  const addModule = async (e) => {
    e.preventDefault();
    const value = newModule.trim();
    if (!value) return;
    if (modules.includes(value)) {
      setNewModule("");
      return;
    }
    try {
      await addAttributeValue("module", value);
      setModules((prev) => [...prev, value].sort());
      setNewModule("");
      showToast(t("devpanel.moduleAdded", { name: value }), { type: "success" });
    } catch (err) {
      showToast(t("devpanel.addModuleError", { message: err.message }), { type: "error" });
    }
  };

  const removeModule = async (value) => {
    if (!window.confirm(t("devpanel.removeModuleConfirm", { name: value }))) return;
    try {
      await deleteAttributeValue("module", value);
      setModules((prev) => prev.filter((m) => m !== value));
      showToast(t("devpanel.moduleRemoved", { name: value }), { type: "success" });
    } catch (err) {
      showToast(t("devpanel.removeModuleError", { message: err.message }), { type: "error" });
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const canPrev = page > 0;
  const canNext = page < totalPages - 1;

  return (
    <section className="panel">
      <h2>{t("devpanel.title")}</h2>

      {canEdit && (
        <div className="dev-modules-block">
          <button
            type="button"
            className="dev-spoiler-toggle"
            onClick={() => setModulesOpen((o) => !o)}
            aria-expanded={modulesOpen}
          >
            <span className="dev-spoiler-arrow">{modulesOpen ? "▾" : "▸"}</span>
            {t("devpanel.modules")}
          </button>
          {modulesOpen && (
            <>
              <div className="dev-modules">
                {modules.map((m) => (
                  <span key={m} className="dev-module-chip">
                    {m}
                    <button
                      className="dev-module-remove"
                      title={t("devpanel.removeModuleTitle", { name: m })}
                      aria-label={t("devpanel.removeModuleAria", { name: m })}
                      onClick={() => removeModule(m)}
                    >
                      ×
                    </button>
                  </span>
                ))}
                {modules.length === 0 && <span className="muted">{t("devpanel.noModules")}</span>}
              </div>
              <form className="dev-module-add" onSubmit={addModule}>
                <input
                  className="dev-input"
                  placeholder={t("devpanel.addModulePlaceholder")}
                  value={newModule}
                  onChange={(e) => setNewModule(e.target.value)}
                />
                <button className="btn" type="submit">
                  {t("devpanel.addModuleBtn")}
                </button>
              </form>
            </>
          )}
        </div>
      )}

      {canEdit && (
        <form className="dev-form" onSubmit={submitCreate}>
          <input
            className="dev-input"
            placeholder={t("devpanel.numberPlaceholder")}
            value={form.number}
            onChange={(e) => setForm((f) => ({ ...f, number: e.target.value }))}
          />
          <input
            className="dev-input"
            placeholder={t("devpanel.namePlaceholder")}
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
          />
          <input
            className="dev-input"
            placeholder={t("devpanel.modulePlaceholder")}
            list="dev-module-datalist"
            value={form.module}
            onChange={(e) => setForm((f) => ({ ...f, module: e.target.value }))}
          />
          <datalist id="dev-module-datalist">
            {modules.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
          <button className="btn" type="submit" disabled={busy}>
            {t("devpanel.addBtn")}
          </button>
        </form>
      )}

      <div className="dev-filter-bar">
        <input
          className="dev-input"
          placeholder={t("devpanel.searchPlaceholder")}
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          aria-label={t("devpanel.searchAria")}
        />
        <select
          className="doc-filter-select"
          value={moduleFilter}
          onChange={(e) => changeModuleFilter(e.target.value)}
          aria-label={t("devpanel.moduleFilterAria")}
        >
          <option value="">{t("devpanel.allModules")}</option>
          <option value={MODULE_NONE}>{t("devpanel.noModule")}</option>
          {modules.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </div>

      <div className="dev-table-scroll">
        <table className="dev-table">
          <thead>
            <tr>
              {COLUMNS.map((c) => (
                <th key={c.key}>
                  <button
                    type="button"
                    className="dev-sort"
                    onClick={() => onSort(c.key)}
                    aria-label={t("devpanel.sortAria", { label: c.label })}
                  >
                    {c.label}
                    {sortIndicator(c.key)}
                  </button>
                </th>
              ))}
              {canEdit && <th />}
            </tr>
          </thead>
          <tbody>
            {developments.map((dev) =>
              editing === dev.id ? (
                <tr key={dev.id}>
                  <td>
                    <input
                      className="dev-input"
                      value={editForm.number}
                      onChange={(e) => setEditForm((f) => ({ ...f, number: e.target.value }))}
                    />
                  </td>
                  <td>
                    <input
                      className="dev-input"
                      value={editForm.name}
                      onChange={(e) => setEditForm((f) => ({ ...f, name: e.target.value }))}
                    />
                  </td>
                  <td>
                    <input
                      className="dev-input"
                      value={editForm.module}
                      list="dev-module-datalist"
                      onChange={(e) => setEditForm((f) => ({ ...f, module: e.target.value }))}
                    />
                  </td>
                  <td>{dev.documents_count}</td>
                  <td className="dev-actions">
                    <button className="btn" onClick={() => submitEdit(dev.id)} disabled={busy}>
                      {t("devpanel.saveBtn")}
                    </button>
                    <button className="btn ghost" onClick={() => setEditing(null)}>
                      {t("devpanel.cancelBtn")}
                    </button>
                  </td>
                </tr>
              ) : (
                <tr key={dev.id}>
                  <td>
                    <Link className="dev-link" href={`/developments/${dev.id}`}>
                      {dev.number}
                    </Link>
                  </td>
                  <td>{dev.display_name || dev.name}</td>
                  <td>{dev.module || "—"}</td>
                  <td>{dev.documents_count}</td>
                  {canEdit && (
                    <td className="dev-actions">
                      <button className="btn ghost" onClick={() => startEdit(dev)}>
                        {t("devpanel.editBtn")}
                      </button>
                      {canDelete && (
                        <button className="btn danger" onClick={() => remove(dev)}>
                          {t("devpanel.deleteBtn")}
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              )
            )}
            {developments.length === 0 && (
              <tr>
                <td colSpan={5} className="dev-empty">
                  {total === 0 && (search || moduleFilter)
                    ? t("devpanel.emptyNotFound")
                    : t("devpanel.emptyEmpty")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {total > 0 && (
        <div className="dev-pagination">
          <span className="muted">{t("devpanel.total", { count: total })}</span>
          <button className="btn ghost" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={!canPrev}>
            {t("devpanel.prev")}
          </button>
          <span className="dev-page-indicator">
            {t("devpanel.page", { page: page + 1, total: totalPages })}
          </span>
          <button className="btn ghost" onClick={() => setPage((p) => p + 1)} disabled={!canNext}>
            {t("devpanel.next")}
          </button>
        </div>
      )}

      {pendingDelete && (
        <ConfirmModal
          count={pendingDelete.documents_count || 0}
          matchValue={pendingDelete.number}
          title={t("devpanel.confirmDeleteTitle")}
          description={
            t("devpanel.confirmDeleteDesc", {
              number: pendingDelete.number,
              name: pendingDelete.name,
              count: pendingDelete.documents_count || 0,
            })
          }
          hint={t("devpanel.confirmDeleteHint", { number: pendingDelete.number })}
          actionLabel={t("devpanel.deleteBtn")}
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => doDelete(pendingDelete)}
        />
      )}
    </section>
  );
}
