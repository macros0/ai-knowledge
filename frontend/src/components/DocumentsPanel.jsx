"use client";

import { useState } from "react";
import TagPicker from "./TagPicker";
import UploadZone from "./UploadZone";
import DocumentList from "./DocumentList";
import { useAuth } from "@/context/AuthContext";

export default function DocumentsPanel() {
  const { mode, hasRole } = useAuth();
  const [uploadTags, setUploadTags] = useState([]);
  const [refreshKey, setRefreshKey] = useState(0);

  // Загрузка — мутирующее действие: только editor/admin (в disabled — все).
  const canUpload = mode === "disabled" || hasRole("editor", "admin");

  return (
    <section className="panel">
      {canUpload && (
        <>
          <TagPicker
            label="Теги для документа (глобальные):"
            placeholder="Введите тег и нажмите Enter..."
            selected={uploadTags}
            onChange={setUploadTags}
            refreshKey={refreshKey}
          />
          <UploadZone
            tags={uploadTags}
            onUploaded={() => setRefreshKey((k) => k + 1)}
          />
        </>
      )}
      <DocumentList refreshKey={refreshKey} />
    </section>
  );
}
