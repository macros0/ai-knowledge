import Link from "next/link";
import TableSourceView from "@/components/TableSourceView";
import { backendFetch } from "@/lib/backendFetch";
import { serverTranslator } from "@/i18n/server";

export const dynamic = "force-dynamic";

export default async function TablePage({ params, searchParams }) {
  const { docId, tableId } = await params;
  const query = await searchParams;
  const { t } = await serverTranslator();
  const generation = typeof query.generation_id === "string" ? query.generation_id : "";
  const textStart = Math.max(0, Number(query.text_start) || 0);
  const textEnd = Number(query.text_end) || null;
  const row = Math.max(1, Number(query.row) || 1);
  const columns = typeof query.columns === "string" ? query.columns.split(",").map(Number).filter(c => Number.isInteger(c) && c > 0 && c <= 16384).slice(0, textStart > 0 ? 1 : 12) : [];
  const sourceId = typeof query.source_id === "string" ? query.source_id : "root";
  let initialData = null;
  if (/^[0-9a-f]{32}$/.test(generation) && /^[0-9a-f]{32}$/.test(tableId)) {
    const qs = new URLSearchParams({generation_id: generation, source_id: sourceId});
    const rowsQuery = new URLSearchParams({...Object.fromEntries(qs), from_row: String(row), limit: textStart > 0 ? "1" : "10",text_start:String(textStart)});
    if (columns.length) rowsQuery.set("columns", columns.join(","));
    const base = `/api/documents/${encodeURIComponent(docId)}/tables/${tableId}`;
    const [descriptor, rows] = await Promise.all([backendFetch(`${base}?${qs}`), backendFetch(`${base}/rows?${rowsQuery}`)]);
    if (descriptor.ok && rows.ok) initialData = {...await descriptor.json(), rows: await rows.json()};
  }
  return <main className="container" style={{minWidth:0,maxWidth:"100%"}}>
    <Link href={`/documents/${encodeURIComponent(docId)}/okf`}>{t("tables.diagnostics")}</Link>
    {initialData ? <TableSourceView docId={docId} initialData={initialData} tableRefs={[{generation_id:generation,table_id:tableId,source_id:sourceId,row_number:row,columns,text_start:textStart,text_end:textEnd}]} />
      : <p role="status">{t("tables.unavailable")}</p>}
  </main>;
}
