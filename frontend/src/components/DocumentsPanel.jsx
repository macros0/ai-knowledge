"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import DocumentList from "./DocumentList";
import DocumentQueueStatus from "./DocumentQueueStatus";
import InterruptedDocumentsNotice from "./InterruptedDocumentsNotice";
import TrashPanel from "./TrashPanel";
import DocumentUploadPanel from "./DocumentUploadPanel";
import UploadReviewDialog from "./UploadReviewDialog";
import useDocumentUpload from "@/hooks/useDocumentUpload";
import { listDevelopments, listActiveLocales } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { useI18n } from "@/i18n/LocaleContext";

export default function DocumentsPanel() {
  const { mode, hasRole } = useAuth();
  const { t, locale } = useI18n();
  const [tagLocale, setTagLocale] = useState(locale);
  const router = useRouter();
  const searchParams = useSearchParams();
  const [uploadTags, setUploadTags] = useState([]);
  const [refreshKey, setRefreshKey] = useState(0);
  const [developments, setDevelopments] = useState([]);
  const [activeLocales, setActiveLocales] = useState([]);
  const upload = useDocumentUpload({ onUploaded: () => setRefreshKey((key) => key + 1) });
  // Переключатель «Документы» / «Корзина» (Этап 4a.2).
  const [view, setView] = useState("docs");

  // Префилл из чата (Этап 4a.1): читается один раз при монтировании.
  const [uploadDevId, setUploadDevId] = useState(() => {
    const raw = searchParams.get("upload_dev");
    const n = raw == null ? NaN : Number(raw);
    return Number.isFinite(n) && n > 0 ? n : null;
  });
  const [uploadModule] = useState(() => searchParams.get("upload_module") || "");

  useEffect(() => {
    let cancelled = false;
    listActiveLocales().then((items) => { if (!cancelled) setActiveLocales(items); }).catch(() => {});
    listDevelopments()
      .then((devs) => {
        if (!cancelled) setDevelopments(devs);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  // Снять query-параметры после применения префилла (одноразовый переход).
  useEffect(() => {
    if (searchParams.has("upload_dev") || searchParams.has("upload_module")) {
      router.replace("/", { scroll: false });
    }
  }, [searchParams, router]);

  // Загрузка — мутирующее действие: только editor/admin (в disabled — все).
  const canUpload = mode === "disabled" || hasRole("editor", "admin");
  const isAdmin = mode === "disabled" || hasRole("admin");

  return (
    <section className="panel">
      <div className="doc-view-toggle">
        <button
          type="button"
          className={`view-btn${view === "docs" ? " active" : ""}`}
          onClick={() => { setView("docs"); setRefreshKey((key) => key + 1); }}
        >
          {t("docs.view.documents")}
        </button>
        {canUpload && <button type="button" className={`view-btn${view === "upload" ? " active" : ""}`} onClick={() => setView("upload")}>
          {t("docs.uploadAction")}
          {upload.busy && <span className="upload-tab-progress">{t("upload.batchProgress", { current: upload.progress.completed, total: upload.progress.total })}</span>}
        </button>}
        <button
          type="button"
          className={`view-btn${view === "trash" ? " active" : ""}`}
          onClick={() => setView("trash")}
        >
          {t("docs.view.trash")}
        </button>
        {view === "docs" && canUpload && <DocumentQueueStatus refreshKey={refreshKey} />}
      </div>
      {view === "docs" && isAdmin && <InterruptedDocumentsNotice refreshKey={refreshKey} onRestored={() => setRefreshKey((key) => key + 1)} />}
      {view === "upload" && canUpload && <DocumentUploadPanel upload={upload}
        tags={uploadTags} onTagsChange={setUploadTags} developmentId={uploadDevId} onDevelopmentChange={setUploadDevId}
        developments={developments} canonicalLocale={tagLocale || locale} onLocaleChange={setTagLocale}
        activeLocales={activeLocales} uploadModule={uploadModule} refreshKey={refreshKey} />}
      {view === "trash" && <TrashPanel />}
      <div className="documents-list-view" hidden={view !== "docs"} inert={view !== "docs" ? true : undefined}>
        <DocumentList refreshKey={refreshKey} onOpenTrash={() => setView("trash")} />
      </div>
      <UploadReviewDialog review={upload.review} onDecision={upload.resolveReview} />
    </section>
  );
}
