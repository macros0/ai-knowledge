"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import TagPicker from "./TagPicker";
import UploadZone from "./UploadZone";
import DocumentList from "./DocumentList";
import TrashPanel from "./TrashPanel";
import DevelopmentPicker from "./DevelopmentPicker";
import { listDevelopments } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

export default function DocumentsPanel() {
  const { mode, hasRole } = useAuth();
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
          Документы
        </button>
        <button
          type="button"
          className={`view-btn${view === "trash" ? " active" : ""}`}
          onClick={() => setView("trash")}
        >
          Корзина
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
                aria-label="Загрузка документов"
              >
                <span>Загрузка документов</span>
                <span className="spoiler-chevron">{uploadOpen ? "▾" : "▸"}</span>
              </button>
              {uploadOpen && (
                <div className="upload-spoiler-body">
                  {uploadModule && uploadDevId == null && (
                    <div className="upload-module-hint">
                      Предзаполнено из чата: модуль <strong>{uploadModule}</strong> — привяжите разработку (необязательно).
                    </div>
                  )}
                  <TagPicker
                    label="Теги для документа (глобальные):"
                    placeholder="Введите тег и нажмите Enter..."
                    selected={uploadTags}
                    onChange={setUploadTags}
                    refreshKey={refreshKey}
                  />
                  {developments.length > 0 && (
                    <div className="upload-dev-row">
                      <span className="tag-picker-label">Разработка для загрузки:</span>
                      <DevelopmentPicker
                        developments={developments}
                        value={uploadDevId}
                        onChange={setUploadDevId}
                      />
                    </div>
                  )}
                  <UploadZone
                    tags={uploadTags}
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
