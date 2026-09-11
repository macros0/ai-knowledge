"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import TagPicker from "./TagPicker";
import ReferenceLocaleSelect from "./ReferenceLocaleSelect";
import UploadZone from "./UploadZone";
import DocumentList from "./DocumentList";
import TrashPanel from "./TrashPanel";
import DevelopmentPicker from "./DevelopmentPicker";
import { listDevelopments } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { useI18n } from "@/i18n/LocaleContext";

export default function DocumentsPanel() {
  const { mode, hasRole } = useAuth();
  const { t, locale } = useI18n();
  const [tagLocale, setTagLocale] = useState(null);
  const router = useRouter();
  const searchParams = useSearchParams();
  const [uploadTags, setUploadTags] = useState([]);
  const [refreshKey, setRefreshKey] = useState(0);
  const [developments, setDevelopments] = useState([]);
  // Область загрузки свёрнута под спойлер (загрузка — не частая операция),
  // раскрывается по клику на заголовок-кнопку.
  const [uploadOpen, setUploadOpen] = useState(false);
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

  return (
    <section className="panel">
      <div className="doc-view-toggle">
        <button
          type="button"
          className={`view-btn${view === "docs" ? " active" : ""}`}
          onClick={() => setView("docs")}
        >
          {t("docs.view.documents")}
        </button>
        <button
          type="button"
          className={`view-btn${view === "trash" ? " active" : ""}`}
          onClick={() => setView("trash")}
        >
          {t("docs.view.trash")}
        </button>
      </div>
      {view === "trash" ? (
        <TrashPanel />
      ) : (
        <>
          {canUpload && (
            <div className="upload-spoiler">
              <button
                type="button"
                className="spoiler-toggle"
                onClick={() => setUploadOpen((v) => !v)}
                aria-expanded={uploadOpen}
                aria-label={t("docs.uploadAria")}
              >
                <span>{t("docs.uploadTitle")}</span>
                <span className="spoiler-chevron">{uploadOpen ? "▾" : "▸"}</span>
              </button>
              {uploadOpen && (
                <div className="upload-spoiler-body">
                  {uploadModule && uploadDevId == null && (
                    <div className="upload-module-hint">
                      {t("docs.uploadModuleHint", { module: uploadModule })}
                    </div>
                  )}
                  <ReferenceLocaleSelect value={tagLocale} onChange={setTagLocale} />
                  <TagPicker
                    collapsible
                    label={t("docs.uploadTagsLabel")}
                    placeholder={t("docs.uploadTagsPlaceholder")}
                    selected={uploadTags}
                    onChange={setUploadTags}
                    refreshKey={refreshKey}
                  />
                  {developments.length > 0 && (
                    <div className="upload-dev-row">
                      <span className="tag-picker-label">{t("docs.uploadDevLabel")}</span>
                      <DevelopmentPicker
                        developments={developments}
                        value={uploadDevId}
                        onChange={setUploadDevId}
                      />
                    </div>
                  )}
                  <UploadZone
                    tags={uploadTags}
                    canonicalLocale={tagLocale || locale}
                    developmentId={uploadDevId}
                    onUploaded={() => setRefreshKey((k) => k + 1)}
                  />
                </div>
              )}
            </div>
          )}
          <DocumentList refreshKey={refreshKey} onOpenTrash={() => setView("trash")} />
        </>
      )}
    </section>
  );
}
