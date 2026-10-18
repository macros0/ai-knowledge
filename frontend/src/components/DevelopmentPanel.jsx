"use client";

import { apiToast } from "@/lib/apiToast.mjs";

import { useCallback, useEffect, useState, useRef } from "react";
import Link from "next/link";
import {
  addAttributeValue,
  createDevelopment,
  deleteAttributeValue,
  deleteDevelopment,
  friendlyApiError,
  getAttributeCatalog,
  getDevelopment,
  listDevelopmentsPage,
  updateDevelopment,
  acquireReferenceEditLock,
  getReferenceEditStatus,
  releaseReferenceEditLock,
} from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { TYPED_CONFIRM_THRESHOLD } from "@/lib/constants";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";
import ConfirmModal from "./ConfirmModal";
import ReferenceLocaleSelect from "./ReferenceLocaleSelect";
import SearchableSelect from "./SearchableSelect";
import ReferenceConflictDialog from "./ReferenceConflictDialog";
import ReferenceEditNotice from "./ReferenceEditNotice";
import { useReferenceEditSession } from "@/hooks/useReferenceEditSession";
import { createEditorToken } from "@/lib/editorLeaseSession.mjs";
import { referenceMutationHeaders } from "@/lib/editorLeaseAdapters.mjs";

const EMPTY_FORM = { number: "", name: "", module: "" };
const PAGE_SIZE = 50;
const MODULE_NONE = "__none__";

export default function DevelopmentPanel() {
  const { mode, hasRole } = useAuth();
  const { showToast } = useToast();
  const { t, locale } = useI18n();
  const [originLocale, setOriginLocale] = useState(locale);
  const [moduleLocale, setModuleLocale] = useState(locale);
  const [developments, setDevelopments] = useState([]);
  const [total, setTotal] = useState(0);
  const [modules, setModules] = useState([]);
  const [newModule, setNewModule] = useState("");
  const [moduleRevision, setModuleRevision] = useState(null);
  const moduleLoadSequence = useRef(0);
  const [form, setForm] = useState(EMPTY_FORM);
  const [editing, setEditing] = useState(null); // id редактируемой разработки
  const [editForm, setEditForm] = useState(EMPTY_FORM);
  const [editBase, setEditBase] = useState(null);
  const [editConflict, setEditConflict] = useState(null);
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
  const editSession = useReferenceEditSession({ resource: { resource_type: "development", resource_id: String(editing || 0) },
    enabled: Boolean(editing) && canEdit, onAcquired: async (_status, isCurrent) => {
      const fresh = await getDevelopment(editing);
      if (!isCurrent()) return;
      if (fresh.reference_revision !== editBase?.reference_revision) {
        setEditConflict({base: editBase, local: {...editForm}, remote: fresh});
      }
    }});

  const COLUMNS = [
    { key: "number", label: t("devpanel.colNumber") },
    { key: "name", label: t("devpanel.colName") },
    { key: "module", label: t("devpanel.colModule") },
    { key: "documents_count", label: t("devpanel.colDocs") },
  ];

  const moduleOptions = [
    { value: "", label: t("devpanel.allModules") },
    { value: MODULE_NONE, label: t("devpanel.noModule"), searchText: t("devpanel.noModule") },
    ...modules.map((module) => ({ value: module, label: module, searchText: module })),
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
      showToast({ ...apiToast(err, t, { type: "error" }), message: t("devpanel.loadError", { message: friendlyApiError(err, t) }) });
    }
  }, [search, moduleFilter, sortBy, sortOrder, page, showToast, t]);

  const loadModules = useCallback(async () => {
    try {
      const sequence = ++moduleLoadSequence.current;
      const snapshot = await getAttributeCatalog("module");
      if (sequence === moduleLoadSequence.current) {
        setModules([...new Set(snapshot.values.map(v => v.value))]);
        setModuleRevision(snapshot.reference_revision);
      }
    } catch { /* Preserve the last observed catalog and any typed module. */ }
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
    async (value, sourceLocale = moduleLocale || locale) => {
      const v = (value || "").trim();
      if (!v || modules.includes(v)) return v || null;
      try {
        const saved = await addAttributeValue("module", v, { label: v, canonicalLocale: sourceLocale, expected_reference_revision: moduleRevision });
        ++moduleLoadSequence.current; setModuleRevision(saved.reference_revision);
        setModules((prev) => [...new Set([...prev, v])].sort());
      } catch (err) {
        if (err.code === "version_conflict") await loadModules();
        showToast({ ...apiToast(err, t, { type: "error" }), message: t("devpanel.addModuleError", { message: friendlyApiError(err, t) }) });
        throw err;
      }
      return v;
    },
    [modules, showToast, t, moduleLocale, locale, moduleRevision, loadModules]
  );

  const submitCreate = async (e) => {
    e.preventDefault();
    const number = form.number.trim();
    const name = form.name.trim();
    if (!number || !name) return;
    setBusy(true);
    try {
      const moduleName = await ensureModule(form.module, originLocale || locale);
      await createDevelopment({ number, name, module: moduleName, canonical_locale: originLocale || locale });
      setForm(EMPTY_FORM);
      showToast(t("devpanel.created", { name }), { type: "success" });
      await load();
    } catch (err) {
      showToast({ ...apiToast(err, t, { type: "error" }), message: t("devpanel.createError", { message: friendlyApiError(err, t) }) });
    } finally {
      setBusy(false);
    }
  };

  const startEdit = (dev) => {
    if (busy) return;
    setEditBase({ ...dev });
    setEditConflict(null);
    setEditing(dev.id);
    setEditForm({
      number: dev.number,
      name: dev.name,
      module: dev.module || "",
      originalNumber: dev.number,
      documentsCount: dev.documents_count,
      version: dev.version,
      reference_revision: dev.reference_revision,
    });
  };

  const submitEdit = async (devId) => {
    if (busy || editConflict || editSession.state !== "editing") return;
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
      const moduleName = await ensureModule(editForm.module);
      await updateDevelopment(devId, {
        number,
        name,
        module: moduleName,
        version: editForm.version,
        expected_reference_revision: editForm.reference_revision,
      }, { headers: editSession.getMutationHeaders() });
      setEditing(null);
      setEditForm(EMPTY_FORM);
      showToast(t("devpanel.saved"), { type: "success" });
      await load();
    } catch (err) {
      editSession.handleMutationError?.(err);
      if (err.code === "version_conflict") {
        const remote = err.data?.current;
        if (remote?.id === devId && Number.isInteger(remote.version)) {
          setEditConflict({ base: editBase, local: { ...editForm, number, name }, remote });
        }
        showToast(t("devpanel.versionConflict"), { type: "error" });
      } else {
        showToast({ ...apiToast(err, t, { type: "error" }), message: t("devpanel.saveError", { message: friendlyApiError(err, t) }) });
      }
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async (dev) => {
    setBusy(true);
    const resource = { resource_type: "development", resource_id: String(dev.id) };
    let context = null;
    try {
      const token = createEditorToken();
      const status = await getReferenceEditStatus(resource);
      const acquired = status.lease_required === false ? status : await acquireReferenceEditLock(resource, { edit_token: token });
      context = acquired.lease_required === false ? {} : { edit_token: token, lease_epoch: acquired.lease_epoch };
      await deleteDevelopment(dev.id, dev.version, { expected_reference_revision: dev.reference_revision,
        headers: referenceMutationHeaders(context) });
      showToast(t("devpanel.deleted"), { type: "success" });
      setPendingDelete(null);
      await load();
    } catch (err) {
      if (err.code === "version_conflict") {
        showToast(t("devpanel.deleteVersionConflict"), { type: "error" });
        setPendingDelete(null);
        await load();
      } else {
        showToast({ ...apiToast(err, t, { type: "error" }), message: t("devpanel.deleteError", { message: friendlyApiError(err, t) }) });
      }
    } finally {
      if (context?.edit_token) releaseReferenceEditLock(resource, context).catch(() => {});
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
      const saved = await addAttributeValue("module", value, { label: value, canonicalLocale: moduleLocale || locale, expected_reference_revision: moduleRevision });
      ++moduleLoadSequence.current; setModuleRevision(saved.reference_revision);
      setModules((prev) => [...new Set([...prev, value])].sort());
      setNewModule("");
      showToast(t("devpanel.moduleAdded", { name: value }), { type: "success" });
    } catch (err) {
      if (err.code === "version_conflict") await loadModules();
      showToast({ ...apiToast(err, t, { type: "error" }), message: t("devpanel.addModuleError", { message: friendlyApiError(err, t) }) });
    }
  };

  const removeModule = async (value) => {
    if (!window.confirm(t("devpanel.removeModuleConfirm", { name: value }))) return;
    try {
      await deleteAttributeValue("module", value, {expected_reference_revision: moduleRevision});
      await loadModules();
      showToast(t("devpanel.moduleRemoved", { name: value }), { type: "success" });
    } catch (err) {
      if (err.code === "version_conflict") await loadModules();
      showToast({ ...apiToast(err, t, { type: "error" }), message: t("devpanel.removeModuleError", { message: friendlyApiError(err, t) }) });
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
                <ReferenceLocaleSelect value={moduleLocale} onChange={setModuleLocale} />
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
          <ReferenceLocaleSelect value={originLocale} onChange={setOriginLocale} />
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
        <SearchableSelect
          options={moduleOptions}
          value={moduleFilter}
          onChange={changeModuleFilter}
          searchPlaceholder={t("docs.optionSearchPlaceholder")}
          emptyLabel={t("docs.empty")}
          ariaLabel={t("devpanel.moduleFilterAria")}
        />
      </div>

      {editing && <ReferenceEditNotice session={editSession}
        onCopy={() => navigator.clipboard?.writeText(JSON.stringify(editForm, null, 2)).catch(() => {})} />}
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
                <tr key={dev.id} onPointerDownCapture={editSession.markActivity}
                  onKeyDownCapture={editSession.markActivity} onScrollCapture={editSession.markActivity}>
                  <td>
                    <input
                      className="dev-input"
                      value={editForm.number}
                      disabled={busy || Boolean(editConflict) || editSession.state !== "editing"}
                      onChange={(e) => setEditForm((f) => ({ ...f, number: e.target.value }))}
                    />
                  </td>
                  <td>
                    <input
                      className="dev-input"
                      value={editForm.name}
                      disabled={busy || Boolean(editConflict) || editSession.state !== "editing"}
                      onChange={(e) => setEditForm((f) => ({ ...f, name: e.target.value }))}
                    />
                  </td>
                  <td>
                    <input
                      className="dev-input"
                      value={editForm.module}
                      disabled={busy || Boolean(editConflict) || editSession.state !== "editing"}
                      list="dev-module-datalist"
                      onChange={(e) => setEditForm((f) => ({ ...f, module: e.target.value }))}
                    />
                  </td>
                  <td>{dev.documents_count}</td>
                  <td className="dev-actions">
                    <button className="btn" onClick={() => submitEdit(dev.id)} disabled={busy || Boolean(editConflict) || editSession.state !== "editing"}>
                      {t("devpanel.saveBtn")}
                    </button>
                    <button className="btn ghost" disabled={busy} onClick={() => { setEditing(null); setEditConflict(null); }}>
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
                      <button className="btn ghost" disabled={busy} onClick={() => startEdit(dev)}>
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

      {editConflict && <ReferenceConflictDialog
        key={`${editing}:${editConflict.remote.version}`}
        {...editConflict}
        fields={["number", "name", "module"]}
        labels={{ number: t("devpanel.colNumber"), name: t("devpanel.colName"), module: t("devpanel.colModule") }}
        onClose={() => setEditConflict(null)}
        onConfirm={merged => {
          setEditForm({ ...editConflict.local, ...merged, version: editConflict.remote.version,
            reference_revision: editConflict.remote.reference_revision,
            originalNumber: editConflict.remote.number, documentsCount: editConflict.remote.documents_count });
          setEditBase({ ...editConflict.remote });
          setEditConflict(null);
        }}
      />}

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
