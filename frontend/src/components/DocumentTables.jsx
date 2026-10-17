"use client";
import {useEffect, useState} from "react";
import {getDocumentTables} from "@/lib/api";
import {useI18n} from "@/i18n/LocaleContext";
import {tableStatusKey} from "@/lib/tableSourceStatus.mjs";

export default function DocumentTables({docId}) {
  const {t} = useI18n();
  const [tables, setTables] = useState(null);
  const [failed, setFailed] = useState(false);
  const [opened, setOpened] = useState(false);
  useEffect(() => {
    if (!opened) return;
    let cancelled = false;
    getDocumentTables(docId).then(data => {if (!cancelled) setTables(data.tables || []);})
      .catch(() => {if (!cancelled) setFailed(true);});
    return () => {cancelled = true;};
  }, [docId, opened]);
  return <details id="tables" onToggle={event => setOpened(event.currentTarget.open)}>
    <summary>{t("tables.diagnostics")}</summary>
    {failed ? <p role="status">{t("tables.unavailable")}</p> : tables === null ? <p>{t("tables.loading")}</p>
      : !tables.length ? <p>{t("tables.coverageUnknown")}</p> : <ul>{tables.map(table => {
        const query = new URLSearchParams({generation_id:table.generation_id,source_id:table.source_id,row:String(table.range.min_row)});
        return <li key={table.table_id}><a href={`/documents/${encodeURIComponent(docId)}/tables/${table.table_id}?${query}`}>{table.sheet_name}</a>
          <p>{t(tableStatusKey(table.quality))}</p>
          <p>{t("tables.coverage", {rows:table.quality.row_count ?? "?",cells:table.quality.cell_count ?? "?"})}</p></li>;
      })}</ul>}
  </details>;
}
