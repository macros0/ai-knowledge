"use client";

import { useState } from "react";
import { useI18n } from "@/i18n/LocaleContext";
import { validRequestId } from "@/lib/diagnosticIdentifiers.mjs";

export function ErrorReferenceView({ requestId, localReportId, translate }) {
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const server = validRequestId(requestId);
  const id = server ? requestId : validRequestId(localReportId) ? localReportId : null;
  if (!id) return null;
  async function copy() {
    try { await navigator.clipboard.writeText(id); setCopied(true); setCopyFailed(false); }
    catch { setCopyFailed(true); }
  }
  return <div className="error-reference">
    <span>{translate(server ? "diagnostics.errorReference" : "diagnostics.localReference")}: </span>
    <code>{id}</code>{" "}
    <button type="button" className="btn ghost" onClick={copy}>{translate(copied ? "diagnostics.copied" : "diagnostics.copy")}</button>
    {copyFailed && <span role="status">{translate("diagnostics.copyFailed")}</span>}
  </div>;
}

export default function ErrorReference(props) {
  const { t } = useI18n();
  return <ErrorReferenceView {...props} translate={t} />;
}
