"use client";

import { useI18n } from "@/i18n/LocaleContext";
import { useEffect, useState } from "react";
import { getDocumentSources } from "@/lib/api";
import { sourceMailMeta } from "@/lib/sourceTree.mjs";

export default function MailMetadata({ source }) {
  const { t, fmtMailDateTime } = useI18n();
  const text = sourceMailMeta(source, fmtMailDateTime, t);
  return text ? <span className="mail-metadata">{text}</span> : null;
}

export function DocumentMailMetadata({ doc }) {
  const [root, setRoot] = useState(null);
  // An older running backend has no batched mail field yet. Read canonical
  // headers for likely mail uploads until it is restarted; filename only
  // controls the request, while the returned source controls attribution.
  const needsRoot = doc.mail === undefined && /\.(eml|msg)$/i.test(doc.filename || "");
  useEffect(() => {
    if (!needsRoot) return;
    let cancelled = false;
    getDocumentSources(doc.id).then((data) => {
      if (!cancelled) setRoot({ docId: doc.id, source: data.sources?.find((item) => item.source_id === "root") });
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [doc.id, needsRoot]);
  const source = doc.mail
    ? { kind: "mail", metadata: doc.mail }
    : root?.docId === doc.id ? root.source : null;
  return <MailMetadata source={source} />;
}
