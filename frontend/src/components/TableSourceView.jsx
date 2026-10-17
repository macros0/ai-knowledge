"use client";

import { useEffect, useState } from "react";
import { getDocumentTable, getDocumentTableRows } from "@/lib/api";
import { tableStatusKey, mergeTablePages } from "@/lib/tableSourceStatus.mjs";
import { useI18n } from "@/i18n/LocaleContext";

function columnLetter(index) {
  let value = index, result = "";
  while (value > 0) { value--; result = String.fromCharCode(65 + value % 26) + result; value = Math.floor(value / 26); }
  return result;
}

export default function TableSourceView({ docId, tableRefs = [], initialData = null }) {
  const { t } = useI18n();
  const ref = tableRefs[0];
  const [data, setData] = useState(initialData);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [columnPage, setColumnPage] = useState(null);
  const [extraCells, setExtraCells] = useState({});
  const tableId = ref?.table_id;
  const generationId = ref?.generation_id;
  const rowNumber = ref?.row_number || 1;
  const originalColumns = (ref?.text_start > 0 ? ref.columns?.slice(0, 1) : ref?.columns)?.join(",");
  const sourceId = ref?.source_id;

  useEffect(() => {
    if (initialData || !tableId || !generationId) return;
    let cancelled = false;
    Promise.all([
      getDocumentTable(docId, tableId, generationId, sourceId),
      getDocumentTableRows(docId, tableId, generationId, { from_row: rowNumber, limit: ref?.text_start > 0 ? 1 : 10, columns: originalColumns, source_id: sourceId, text_start: ref?.text_start || 0 }),
    ]).then(([descriptor, rows]) => {
      if (!cancelled) setData({ ...descriptor, rows });
    }).catch(() => { if (!cancelled) setError(true); });
    return () => { cancelled = true; };
  }, [docId, tableId, generationId, rowNumber, sourceId, originalColumns, initialData, ref?.text_start]);

  const load = async (options, appendRows = false) => {
    setBusy(true);
    setError(false);
    try {
      const rows = await getDocumentTableRows(docId, tableId, generationId, { source_id: sourceId, limit: 10, ...options });
      if (appendRows) {
        if (columnPage) setColumnPage(previous => mergeTablePages(previous, rows));
        else setData(previous => ({ ...previous, rows: mergeTablePages(previous.rows, rows) }));
      }
      else { setColumnPage(rows); setExtraCells({}); }
    } catch { setError(true); }
    finally { setBusy(false); }
  };
  const moreCell = async (continuation) => {
    setBusy(true);
    try {
      const rows = await getDocumentTableRows(docId, tableId, generationId, { source_id: sourceId, limit: 1,
        from_row: continuation.row_number, from_column: continuation.column, columns: String(continuation.column),
        text_start: continuation.next_text_start });
      const key = `${continuation.row_number}:${continuation.column}`;
      const cell = rows.rows[0]?.cells?.[String(continuation.column)];
      setExtraCells(previous => ({ ...previous, [key]: (previous[key] || "") + (cell?.display_text || "") }));
      const next = rows.continuation[0];
      setColumnPage(previous => ({ ...(previous || data.rows), continuation: (previous || data.rows).continuation.map(item =>
        item.row_number === continuation.row_number && item.column === continuation.column ? (next || { ...item, next_text_start: null }) : item) }));
    } catch { setError(true); }
    finally { setBusy(false); }
  };

  if (!ref || error && !data) return <p role="status">{t("tables.unavailable")}</p>;
  if (!data) return <p role="status">{t("tables.loading")}</p>;
  const descriptor = data.descriptor;
  const quality = data.quality || {};
  const page = columnPage || data.rows;
  const columns = page.columns || [];
  const wanted = new Set(tableRefs.filter(r => r.row_number === ref.row_number).flatMap(r => r.columns || []));
  const status = tableStatusKey(quality);
  const causeKey = {
    classifier_timeout: "tables.classifierTimeout",
    classifier_output_truncated: "tables.classifierTruncated",
    classifier_invalid_result: "tables.classifierInvalid",
  }[quality.classifier_cause];
  const fieldLabel = column => column.header_path?.filter(Boolean).join(" / ").trim()
    || t("tables.column", { column: column.letter || columnLetter(column.index) });
  const fieldText = (row, column) => {
    const cell = row.cells[String(column.index)];
    return cell?.state === "formula_missing" ? t("tables.formulaMissing")
      : (cell?.display_text || "") + (extraCells[`${row.row_number}:${column.index}`] || "");
  };
  const renderValue = (row, column) => {
    const continuation = page.continuation?.find(item => item.row_number === row.row_number && item.column === column.index);
    return <>
      <div className="table-source-value">{fieldText(row, column) || <span className="table-source-empty">{t("tables.emptyCell")}</span>}</div>
      {continuation?.next_text_start != null && <button type="button" className="btn" disabled={busy} onClick={() => moreCell(continuation)}>{t("tables.moreCell")}</button>}
    </>;
  };
  return <section className="table-source-view" aria-label={t("tables.title")}>
    <h2>{descriptor.sheet_name}</h2>
    <p className="table-source-status" role="status">{t(status)}</p>
    <details className="table-source-meta">
      <summary>{t("tables.about")}</summary>
      <p>{columnLetter(descriptor.cell_range.min_column)}{descriptor.cell_range.min_row}:{columnLetter(descriptor.cell_range.max_column)}{descriptor.cell_range.max_row}</p>
      {causeKey && <p>{t(causeKey)}</p>}
      <p>{t("tables.coverage", { rows: quality.row_count ?? "?", cells: quality.cell_count ?? "?" })}</p>
      {page.projection && <p>{t("tables.projection")}</p>}
    </details>
    {error && <p role="alert">{t("tables.unavailable")}</p>}
    <div className="table-source-cards">
      {page.rows.map(row => <article key={row.row_number} className={`table-source-card${row.row_number === ref.row_number ? " is-selected" : ""}`} aria-label={t("tables.rowNumber", { row: row.row_number })}>
        <header className="table-source-card-header">
          <h3>{t("tables.rowNumber", { row: row.row_number })}</h3>
          {row.row_number === ref.row_number && <span className="table-source-selected">{t("tables.selectedRow")}</span>}
        </header>
        <dl className="table-source-fields">
          {columns.map(column => {
            const value = fieldText(row, column);
            const continuation = page.continuation?.some(item => item.row_number === row.row_number && item.column === column.index && item.next_text_start != null);
            const long = value.length > 320 || value.split("\n").length > 4 || continuation;
            const isTarget = row.row_number === ref.row_number && wanted.has(column.index);
            return <div key={column.index} className={`table-source-field${long ? " is-long" : ""}${isTarget ? " is-target" : ""}`}>
              <dt>{fieldLabel(column)}</dt>
              <dd>{long ? <details className="table-source-long-value">
                <summary><span className="table-source-expand table-source-show">{t("tables.showText")}</span><span className="table-source-expand table-source-hide">{t("tables.hideText")}</span><span className="table-source-preview">{value.slice(0, 160)}{value.length > 160 ? "…" : ""}</span></summary>
                {renderValue(row, column)}
              </details> : renderValue(row, column)}</dd>
            </div>;
          })}
        </dl>
        <details className="table-source-coordinates">
          <summary>{t("tables.source")}</summary>
          <p>{descriptor.sheet_name} · {t("tables.rowNumber", { row: row.row_number })}</p>
          <dl>{columns.map(column => <div key={column.index}><dt>{fieldLabel(column)}</dt><dd>{row.cells[String(column.index)]?.address || `${column.letter || columnLetter(column.index)}${row.row_number}`}</dd></div>)}</dl>
        </details>
      </article>)}
    </div>
    <details className="table-source-original">
      <summary>{t("tables.originalTable")}</summary>
      <div className="table-source-scroll" tabIndex={0} role="region" aria-label={t("tables.scrollArea")}>
      <table>
        <thead><tr><th>{t("tables.row")}</th>{columns.map(column => <th key={column.index} scope="col">{column.letter}: {column.header_path?.join(" / ")}</th>)}</tr></thead>
        <tbody>{page.rows.map(row => <tr key={row.row_number}>
          <th scope="row">{row.row_number}</th>
          {columns.map(column => {
            const isTarget = row.row_number === ref.row_number && wanted.has(column.index);
            return <td key={column.index} className={isTarget ? "is-target" : undefined}>
              <small>{row.cells[String(column.index)]?.address}</small>
              <div className="table-source-original-value">{renderValue(row, column)}</div>
            </td>;
          })}
        </tr>)}</tbody>
      </table>
      </div>
    </details>
    <div className="table-source-actions">
      {page.next_column == null && originalColumns && !columnPage && <button type="button" disabled={busy} onClick={() => load({ from_row: rowNumber, from_column: descriptor.cell_range.min_column })}>{t("tables.moreColumns")}</button>}
      {page.next_column != null && <button type="button" disabled={busy} onClick={() => load({ from_row: rowNumber, from_column: page.next_column })}>{t("tables.moreColumns")}</button>}
      {columnPage && <button type="button" disabled={busy} onClick={() => { setColumnPage(null); setExtraCells({}); }}>{t("tables.selectedCells")}</button>}
      {page.next_row != null && <button type="button" disabled={busy} onClick={() => load({ from_row: page.next_row, columns: columns.map(column => column.index).join(",") }, true)}>{t("tables.moreRows")}</button>}
    </div>
  </section>;
}
