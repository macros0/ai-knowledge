"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ApiError, friendlyApiError, getDevelopment, listDevelopmentDocuments } from "@/lib/api";
import { useToast } from "@/components/Toast";
import { useI18n } from "@/i18n/LocaleContext";
import SearchableSelect from "@/components/SearchableSelect";

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 300;

export default function DevelopmentCardPage() {
  const { devId } = useParams();
  const { showToast } = useToast();
  const { t, locale, fmtDate } = useI18n();
  const [dev, setDev] = useState(null);
  const [docs, setDocs] = useState([]);
  const [total, setTotal] = useState(0);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState("date_desc");
  const [page, setPage] = useState(0);
  const loadSeq = useRef(0);

  const STATUS_LABELS = {
    uploaded: t("status.uploaded"),
    queued: t("status.queued"),
    processing: t("status.processing"),
    splitting: t("status.splitting"),
    indexing: t("status.indexing"),
    done: t("status.done"),
    paused: t("status.paused"),
    failed: t("status.failed"),
    error: t("status.error"),
  };

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    try {
      const d = await getDevelopment(devId);
      const result = await listDevelopmentDocuments(devId, {
        search: search || undefined,
        sort: sortKey,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      });
      if (seq !== loadSeq.current) return;
      setDev(d);
      setDocs(result.documents);
      setTotal(result.total);
    } catch (err) {
      showToast(t("dev.card.loadError", { message: friendlyApiError(err, t) }), { type: "error" });
    }
  }, [devId, search, sortKey, page, showToast, t]);

  // Debounce серверного поиска.
  useEffect(() => {
    const t = setTimeout(() => setSearch(searchInput.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [searchInput]);

  // Смена поиска/сортировки сбрасывает страницу к началу.
  useEffect(() => {
    setPage(0);
  }, [search, sortKey]);

  useEffect(() => {
    load();
  }, [load]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const sortOptions = [
    { value: "date_desc", label: `${t("sort.groupDate")}: ${t("sort.newFirst")}`, searchText: `${t("sort.groupDate")} ${t("sort.newFirst")}` },
    { value: "date_asc", label: `${t("sort.groupDate")}: ${t("sort.oldFirst")}`, searchText: `${t("sort.groupDate")} ${t("sort.oldFirst")}` },
    { value: "name_asc", label: `${t("sort.groupName")}: ${t("sort.alphaAsc")}`, searchText: `${t("sort.groupName")} ${t("sort.alphaAsc")}` },
    { value: "name_desc", label: `${t("sort.groupName")}: ${t("sort.alphaDesc")}`, searchText: `${t("sort.groupName")} ${t("sort.alphaDesc")}` },
    { value: "uploader_asc", label: `${t("sort.groupUploader")}: ${t("sort.alphaAsc")}`, searchText: `${t("sort.groupUploader")} ${t("sort.alphaAsc")}` },
    { value: "uploader_desc", label: `${t("sort.groupUploader")}: ${t("sort.alphaDesc")}`, searchText: `${t("sort.groupUploader")} ${t("sort.alphaDesc")}` },
  ];

  return (
    <section className="panel">
      <p className="breadcrumbs">
        <Link href="/developments">{t("dev.card.back")}</Link>
      </p>
      <h2>
        {dev?.number} — {dev?.display_name || dev?.name}
      </h2>
      <p className="meta">
        {t("dev.card.module", { module: dev?.module || "—" })} · {t("dev.card.docsCount", { count: dev?.documents_count })}
      </p>

      <h3>{t("dev.card.docsTitle")}</h3>
      <div className="doc-filter-bar">
        <input
          type="text"
          className="doc-filter-input"
          placeholder={t("docs.searchPlaceholder")}
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          aria-label={t("dev.card.searchAria")}
        />
        <SearchableSelect
          options={sortOptions}
          triggerClassName="doc-filter-select"
          value={sortKey}
          onChange={setSortKey}
          searchPlaceholder={t("sort.label")}
          emptyLabel={t("docs.empty")}
          ariaLabel={t("sort.label")}
        />
      </div>
      {docs.length === 0 ? (
        <p className="muted">
          {search ? t("docs.empty") : t("dev.card.noDocs")}
        </p>
      ) : (
        <ul className="document-list">
          {docs.map((doc) => (
            <li key={doc.id} className="document-item">
              <div>
                <strong>{doc.filename}</strong>
                <div className="meta">
                  {doc.error || doc.error_code ? friendlyApiError(new ApiError("", { code: doc.error_code }), t) : t("docs.metaFile", { size: (doc.size / 1024).toFixed(1), count: doc.okf_concept_count })}
                  {doc.uploaded_by && (
                    <>
                      <br />
                      <span className="doc-uploader">{t("docs.uploadedBy", { name: doc.uploaded_by })}</span>
                    </>
                  )}
                </div>
              </div>
              <div className="doc-actions">
                <span className={`status ${doc.status}`}>
                  {STATUS_LABELS[doc.status] ?? doc.status}
                </span>
                <Link className="icon-btn" href={`/documents/${doc.id}/okf`} title={t("dev.card.openTitle")}>
                  →
                </Link>
              </div>
            </li>
          ))}
        </ul>
      )}
      {totalPages > 1 && (
        <div className="doc-pagination">
          <button
            className="page-btn"
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
          >
            {t("pagination.back")}
          </button>
          <span className="page-indicator">
            {t("pagination.page", { page: page + 1, total: totalPages })}
          </span>
          <button
            className="page-btn"
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
            disabled={page >= totalPages - 1}
          >
            {t("pagination.forward")}
          </button>
        </div>
      )}
    </section>
  );
}
