"use client";

import { useEffect, useState } from "react";
import { getDocumentQueueStatus } from "@/lib/api";
import { documentQueueView } from "@/lib/documentLayout.mjs";
import { useI18n } from "@/i18n/LocaleContext";

export default function DocumentQueueStatus({ refreshKey = 0 }) {
  const { t } = useI18n();
  const [snapshot, setSnapshot] = useState(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let active = true;
    let pending = false;
    async function refresh() {
      if (pending) return;
      pending = true;
      try {
        const next = await getDocumentQueueStatus();
        if (active) setSnapshot(next);
      } catch {
        if (active) setSnapshot(null);
      } finally {
        pending = false;
        if (active) setLoaded(true);
      }
    }
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [refreshKey]);

  const view = documentQueueView(snapshot, t);
  return (
    <div className={`document-queue-status${view?.full ? " document-queue-full" : ""}`} role="status" aria-live="polite">
      <span>{view ? view.summary : t(loaded ? "docs.queue.unknown" : "docs.queue.loading")}</span>
      {view?.full && <strong>{t("docs.queue.full")}</strong>}
    </div>
  );
}
