"use client";

import { useEffect, useState } from "react";
import ru from "@/i18n/locales/ru";
import en from "@/i18n/locales/en";
import { ErrorReferenceView } from "@/components/ErrorReference";
import { browserDiagnosticRegistry } from "@/lib/browserDiagnosticRegistry.mjs";

export default function GlobalError({ error, reset }) {
  const [localId, setLocalId] = useState(null);
  const [locale, setLocale] = useState("en");
  useEffect(() => {
    const id = globalThis.crypto?.randomUUID?.() || null;
    setLocalId(id);
    browserDiagnosticRegistry.reportRootError(error, id);
    setLocale(document.documentElement.lang === "ru" ? "ru" : "en");
  }, [error]);
  const t = (key) => (locale === "ru" ? ru : en)[key] || key;
  return <html lang={locale}><body>
    <main className="app-shell" role="alert">
      <h1>{t("diagnostics.pageFailed")}</h1>
      <p>{t("diagnostics.pageFailedHelp")}</p>
      <ErrorReferenceView requestId={error?.requestId} localReportId={localId} translate={t} />
      <button type="button" onClick={reset}>{t("diagnostics.retry")}</button>
    </main>
  </body></html>;
}
