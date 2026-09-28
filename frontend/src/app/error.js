"use client";

import { useEffect, useState } from "react";
import ErrorReference from "@/components/ErrorReference";
import { useI18n } from "@/i18n/LocaleContext";

export default function ErrorPage({ error, reset }) {
  const { t } = useI18n();
  const [localId, setLocalId] = useState(null);
  useEffect(() => {
    const id = globalThis.crypto?.randomUUID?.() || null;
    setLocalId(id);
    window.dispatchEvent(new CustomEvent("okf-diagnostic-boundary-error", { detail: { error, localReportId: id } }));
  }, [error]);
  return <section className="panel" role="alert">
    <h2>{t("diagnostics.pageFailed")}</h2>
    <p>{t("diagnostics.pageFailedHelp")}</p>
    <ErrorReference requestId={error?.requestId} localReportId={localId} />
    <button type="button" className="btn" onClick={reset}>{t("diagnostics.retry")}</button>
  </section>;
}
