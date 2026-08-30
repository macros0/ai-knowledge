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
import { useToast } from "./Toast";

const EMPTY_FORM = { number: "", name: "", module: "" };
const PAGE_SIZE = 50;
const MODULE_NONE = "__none__";

const COLUMNS = [
  { key: "number", label: "Номер" },
  { key: "name", label: "Название" },
  { key: "module", label: "Модуль" },
  { key: "documents_count", label: "Документов" },
];

export default function DevelopmentPanel() {
  const { mode, hasRole } = useAuth();
  const { showToast } = useToast();
  const [developments, setDevelopments] = useState([]);
  const [total, setTotal] = useState(0);
  const [modules, setModules] = useState([]);
  const [newModule, setNewModule] = useState("");
  const [form, setForm] = useState(EMPTY_FORM);
  const [editing, setEditing] = useState(null); // id редактируемой разработки
  const [editForm, setEditForm] = useState(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [modulesOpen, setModulesOpen] = useState(false);

  // Серверные фильтр/сортировка/пагинация.
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [moduleFilter, setModuleFilter] = useState("");
  const [sortBy, setSortBy] = useState("number");
  const [sortOrder, setSortOrder] = useState("asc");
  const [page, setPage] = useState(0);

  const canEdit = mode === "disabled" || hasRole("editor", "admin");
  const canDelete = mode === "disabled" || hasRole("admin");

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
      showToast(`Не удалось загрузить справочник: ${err.message}`, { type: "error" });
    }
  }, [search, moduleFilter, sortBy, sortOrder, page, showToast]);

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
    const t = setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(0);
    }, 300);
    return () => clearTimeout(t);
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
        setModules((prev) => (prev.includes(v) ? prev : [...prev, v]));
      } catch (err) {
        showToast(`Не удалось добавить модуль: ${err.message}`, { type: "error" });
        throw err;
      }
      return v;
    },
    [modules, showToast]
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
      showToast(`Разработка «${number}» добавлена`, { type: "success" });
      await load();
    } catch (err) {
      showToast(`Не удалось добавить: ${err.message}`, { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  const startEdit = (dev) => {
    setEditing(dev.id);
    setEditForm({ number: dev.number, name: dev.name, module: dev.module || "" });
  };

  const submitEdit = async (devId) => {
    const number = editForm.number.trim();
    const name = editForm.name.trim();
    if (!number || !name) return;
    setBusy(true);
    try {
      const module = await ensureModule(editForm.module);
      await updateDevelopment(devId, { number, name, module });
      setEditing(null);
      setEditForm(EMPTY_FORM);
      showToast("Изменения сохранены", { type: "success" });
      await load();
    } catch (err) {
      showToast(`Не удалось сохранить: ${err.message}`, { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  const remove = async (dev) => {
    if (!window.confirm(`Удалить разработку «${dev.number} — ${dev.name}»? Документы будут отвязаны.`)) {
      return;
    }
    try {
      await deleteDevelopment(dev.id);
      showToast("Разработка удалена", { type: "success" });
      await load();
    } catch (err) {
      showToast(`Не удалось удалить: ${err.message}`, { type: "error" });
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
      showToast(`Модуль «${value}» добавлен`, { type: "success" });
    } catch (err) {
      showToast(`Не удалось добавить модуль: ${err.message}`, { type: "error" });
    }
  };

  const removeModule = async (value) => {
    if (!window.confirm(`Удалить модуль «${value}» из справочника?`)) return;
    try {
      await deleteAttributeValue("module", value);
      setModules((prev) => prev.filter((m) => m !== value));
      showToast(`Модуль «${value}» удалён`, { type: "success" });
    } catch (err) {
      showToast(`Не удалось удалить модуль: ${err.message}`, { type: "error" });
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const canPrev = page > 0;
  const canNext = page < totalPages - 1;

  return (
    <section className="panel">
      <h2>Справочник разработок</h2>

      {canEdit && (
        <div className="dev-modules-block">
          <button
            type="button"
            className="dev-spoiler-toggle"
            onClick={() => setModulesOpen((o) => !o)}
            aria-expanded={modulesOpen}
          >
            <span className="dev-spoiler-arrow">{modulesOpen ? "▾" : "▸"}</span>
            Модули
          </button>
          {modulesOpen && (
            <>
              <div className="dev-modules">
                {modules.map((m) => (
                  <span key={m} className="dev-module-chip">
                    {m}
                    <button
                      className="dev-module-remove"
                      title={`Удалить модуль «${m}»`}
                      aria-label={`Удалить модуль ${m}`}
                      onClick={() => removeModule(m)}
                    >
                      ×
                    </button>
                  </span>
                ))}
                {modules.length === 0 && <span className="muted">Модули не заданы.</span>}
              </div>
              <form className="dev-module-add" onSubmit={addModule}>
                <input
                  className="dev-input"
                  placeholder="Новый модуль (напр. ЛК)"
                  value={newModule}
                  onChange={(e) => setNewModule(e.target.value)}
                />
                <button className="btn" type="submit">
                  Добавить модуль
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
            placeholder="Номер (напр. 12010)"
            value={form.number}
            onChange={(e) => setForm((f) => ({ ...f, number: e.target.value }))}
          />
          <input
            className="dev-input"
            placeholder="Название"
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
          />
          <input
            className="dev-input"
            placeholder="Модуль (напр. PY)"
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
            Добавить
          </button>
        </form>
      )}

      <div className="dev-filter-bar">
        <input
          className="dev-input"
          placeholder="Поиск по номеру или названию…"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          aria-label="Поиск по справочнику"
        />
        <select
          className="doc-filter-select"
          value={moduleFilter}
          onChange={(e) => changeModuleFilter(e.target.value)}
          aria-label="Фильтр по модулю"
        >
          <option value="">Все модули</option>
          <option value={MODULE_NONE}>Без модуля</option>
          {modules.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </div>

      <table className="dev-table">
        <thead>
          <tr>
            {COLUMNS.map((c) => (
              <th key={c.key}>
                <button
                  type="button"
                  className="dev-sort"
                  onClick={() => onSort(c.key)}
                  aria-label={`Сортировать по ${c.label}`}
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
                    Сохранить
                  </button>
                  <button className="btn ghost" onClick={() => setEditing(null)}>
                    Отмена
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
                <td>{dev.name}</td>
                <td>{dev.module || "—"}</td>
                <td>{dev.documents_count}</td>
                {canEdit && (
                  <td className="dev-actions">
                    <button className="btn ghost" onClick={() => startEdit(dev)}>
                      Изменить
                    </button>
                    {canDelete && (
                      <button className="btn danger" onClick={() => remove(dev)}>
                        Удалить
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
                {total === 0 && (search || moduleFilter) ? "Ничего не найдено" : "Справочник пуст. Добавьте первую разработку."}
              </td>
            </tr>
          )}
        </tbody>
      </table>

      {total > 0 && (
        <div className="dev-pagination">
          <span className="muted">Всего: {total}</span>
          <button className="btn ghost" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={!canPrev}>
            ← Пред.
          </button>
          <span className="dev-page-indicator">
            Стр. {page + 1} из {totalPages}
          </span>
          <button className="btn ghost" onClick={() => setPage((p) => p + 1)} disabled={!canNext}>
            След. →
          </button>
        </div>
      )}
    </section>
  );
}
