"use client";

import { useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { useI18n } from "@/i18n/LocaleContext";
import DocumentMarkupModal from "./DocumentMarkupModal";

export default function DocumentMarkupButton({ docId }) {
  const { mode, hasRole } = useAuth();
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  if (!(mode === "disabled" || hasRole("editor", "admin"))) return null;
  return (
    <>
      <button type="button" className="download-btn" onClick={() => setOpen(true)}>
        {t("documentMarkup.button")}
      </button>
      {open && <DocumentMarkupModal key={docId} docId={docId} onClose={() => setOpen(false)} />}
    </>
  );
}
