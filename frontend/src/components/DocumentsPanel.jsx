"use client";

import { useState } from "react";
import TagPicker from "./TagPicker";
import UploadZone from "./UploadZone";
import DocumentList from "./DocumentList";

export default function DocumentsPanel() {
  const [uploadTags, setUploadTags] = useState([]);
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <section className="panel">
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
      <DocumentList refreshKey={refreshKey} />
    </section>
  );
}
