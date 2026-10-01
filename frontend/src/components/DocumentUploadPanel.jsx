"use client";

import { useState } from "react";
import TagPicker from "./TagPicker";
import SearchableSelect from "./SearchableSelect";
import UploadZone from "./UploadZone";
import DevelopmentPicker from "./DevelopmentPicker";
import { useI18n } from "@/i18n/LocaleContext";
import { buildLocaleOptions } from "@/lib/sourceLocales.mjs";

export default function DocumentUploadPanel({ upload, tags, onTagsChange, developmentId,
  onDevelopmentChange, developments, canonicalLocale, onLocaleChange, activeLocales, uploadModule, refreshKey }) {
  const { t, locale } = useI18n();
  const [portalContainer, setPortalContainer] = useState(null);
  const [advanced, setAdvanced] = useState(false);
  const development = developments.find((item) => item.id === developmentId);
  const summary = [tags.join(", "), development ? `${development.number} · ${development.name}` : developmentId].filter(Boolean).join(" · ");
  return (
      <div ref={setPortalContainer} className="document-upload-dialog document-upload-panel">
        <div className="upload-language-row">
          <span>{t("reference.originalLanguage")}</span>
          <SearchableSelect
            options={buildLocaleOptions(canonicalLocale, activeLocales, locale).map((option) => ({ value: option.code, label: option.label }))}
            value={canonicalLocale} onChange={onLocaleChange} disabled={upload.busy}
            ariaLabel={t("reference.originalLanguage")} searchPlaceholder={t("docs.localeSearchPlaceholder")}
            emptyLabel={t("docs.empty")} portalContainer={portalContainer} />
        </div>
        <UploadZone busy={upload.busy} onFiles={(files) => upload.start(files, { tags, developmentId, canonicalLocale })} />
        {upload.busy && <p role="status">{t("upload.batchProgress", { current: upload.progress.completed, total: upload.progress.total })}</p>}
        <button type="button" className="bulk-tag-btn" aria-expanded={advanced} onClick={() => setAdvanced((value) => !value)}>
          {t("upload.advanced")} {advanced ? "▾" : "▸"}
        </button>
        {!advanced && summary && <p className="upload-parameter-summary">{summary}</p>}
        {advanced && <div className="upload-advanced">
          <TagPicker label={t("docs.uploadTagsLabel")} placeholder={t("docs.uploadTagsPlaceholder")}
            selected={tags} onChange={onTagsChange} refreshKey={refreshKey} disabled={upload.busy} portalContainer={portalContainer} />
          <div className="upload-dev-row">
            <span>{t("docs.uploadDevLabel")}</span>
            <DevelopmentPicker developments={developments} value={developmentId} onChange={onDevelopmentChange}
              disabled={upload.busy} portalContainer={portalContainer} />
          </div>
          {uploadModule && developmentId == null && <p className="upload-module-hint">{t("docs.uploadModuleHint", { module: uploadModule })}</p>}
        </div>}
      </div>
  );
}
