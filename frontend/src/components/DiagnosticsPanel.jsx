"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useI18n } from "@/i18n/LocaleContext";
import { useToast } from "./Toast";
import Modal from "./Modal";
import { apiToast } from "@/lib/apiToast.mjs";
import { diagnosticActions, diagnosticBundleRequest, diagnosticGapKey, diagnosticReasonKey, diagnosticStatusKey, remainingSeconds } from "@/lib/diagnostics.mjs";
import { createDiagnosticBundle, deleteDiagnosticBundle, diagnosticDownloadUrl,
  getDiagnosticsStatus, inviteDiagnosticBrowser, listDiagnosticBundles,
  previewDiagnosticBundle, queryDiagnosticEvents, startDiagnosticSession,
  stopDiagnosticSession } from "@/lib/api";

const pending = new Set(["queued", "building"]);
const mib = (bytes) => typeof bytes === "number" ? `${(bytes / 1048576).toFixed(1)} MiB` : "—";
const date = (value) => value ? new Date(value).toLocaleString() : "—";

export default function DiagnosticsPanel() {
  const { t } = useI18n();
  const { showToast } = useToast();
  const [status, setStatus] = useState(null);
  const [stale, setStale] = useState(true);
  const [scope, setScope] = useState("system");
  const [minutes, setMinutes] = useState(15);
  const [docId, setDocId] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [requestId, setRequestId] = useState("");
  const [bundleSessionId, setBundleSessionId] = useState("");
  const [events, setEvents] = useState(null);
  const [bundles, setBundles] = useState([]);
  const [preview, setPreview] = useState(null);
  const [pendingDeleteId, setPendingDeleteId] = useState(null);
  const [invitation, setInvitation] = useState("");
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const polledAt = useRef(0);
  const pendingBundles = useRef(false);

  const loadStatus = useCallback(async () => {
    try {
      const next = await getDiagnosticsStatus();
      polledAt.current = performance.now();
      setElapsed(0); setStatus(next); setStale(false);
    } catch { setStale(true); }
  }, []);
  const loadBundles = useCallback(async () => {
    try {
      const next = (await listDiagnosticBundles()).items || [];
      setBundles(next);
      pendingBundles.current = next.some((item) => pending.has(item.status));
    } catch { /* Keep the last known list. */ }
  }, []);

  useEffect(() => {
    void loadStatus(); void loadBundles();
    const refresh = () => {
      if (document.visibilityState !== "visible") return;
      void loadStatus();
      if (pendingBundles.current) void loadBundles();
    };
    const timer = setInterval(refresh, 3000);
    document.addEventListener("visibilitychange", refresh);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", refresh); };
  }, [loadStatus, loadBundles]);

  useEffect(() => {
    const timer = setInterval(() => setElapsed(Math.max(0, performance.now() - polledAt.current)), 1000);
    return () => clearInterval(timer);
  }, []);

  const actions = diagnosticActions(status, { stale });
  const active = status?.session?.session;
  const remaining = active ? remainingSeconds(active.expires_at, status?.server_now, elapsed) : null;
  const filters = () => ({
    ...(from ? { from_utc: new Date(from).toISOString() } : {}),
    ...(to ? { to_utc: new Date(to).toISOString() } : {}),
    ...(requestId.trim() ? { request_id: requestId.trim() } : {}),
  });
  const action = async (work, { refreshBundles = false } = {}) => {
    setBusy(true);
    try {
      await work();
      await loadStatus();
      if (refreshBundles) await loadBundles();
      return true;
    } catch (error) { showToast(apiToast(error, t, { type: "error", duration: 10000 })); return false; }
    finally { setBusy(false); }
  };
  const confirmDelete = async () => {
    if (!pendingDeleteId || busy) return;
    const id = pendingDeleteId;
    const deleted = await action(() => deleteDiagnosticBundle(id), { refreshBundles: true });
    if (deleted) {
      setPendingDeleteId(null);
      setPreview((current) => current?.id === id ? null : current);
    }
  };

  return <main className="diagnostics-page">
    <div className="panel-head"><h1>{t("diagnostics.title")}</h1><Link href="/admin">{t("diagnostics.back")}</Link></div>
    <p className="muted">{t("diagnostics.privacyNotice")}</p>

    <section className="panel diagnostics-card" aria-label={t("diagnostics.statusTitle")}>
      <h2>{t("diagnostics.statusTitle")}</h2>
      <p role="status">{t(`diagnostics.session.${actions.sessionState}`)}</p>
      {stale && <p role="alert">{t("diagnostics.statusUnavailable")}</p>}
      {status?.runtime?.failure_code && <p role="alert">{t("diagnostics.statusDegraded")}</p>}
      {status?.session?.audit_pending && <p role="alert">{t("diagnostics.auditPending")}</p>}
      {status?.quota?.storage_degraded && <p role="alert">{t("diagnostics.storageLow")}</p>}
      <p>{t("diagnostics.space", { used: mib(status?.quota?.used_bytes), quota: mib(status?.quota?.quota_bytes), free: mib(status?.quota?.free_bytes) })}</p>
      {active && <p>{t("diagnostics.autoStop")}: {date(active.expires_at)} · {remaining ?? "—"} {t("diagnostics.seconds")}</p>}
      {status?.session?.last_session?.stop_reason && !active && <p>{t("diagnostics.stopReason")}: {t(diagnosticReasonKey(status.session.last_session.stop_reason))}</p>}
      {(status?.recorder?.dropped > 0 || status?.recorder?.expired_queue > 0 || status?.recorder?.drain_timeouts > 0) &&
        <p role="alert">{t("diagnostics.partialNotice")}</p>}
      {status?.recorder?.sampled_success > 0 &&
        <p>{t("diagnostics.sampledSuccess", { count: status.recorder.sampled_success })}</p>}
    </section>

    <section className="panel diagnostics-card" aria-label={t("diagnostics.captureTitle")}>
      <h2>{t("diagnostics.captureTitle")}</h2>
      <div className="diagnostics-controls">
        <label>{t("diagnostics.scope")} <select value={scope} onChange={(event) => setScope(event.target.value)} disabled={busy || !!active}>
          {["system", "document", "search_chat", "interface"].map((item) => <option key={item} value={item}>{t(`diagnostics.scope.${item}`)}</option>)}
        </select></label>
        <label>{t("diagnostics.minutes")} <select value={minutes} onChange={(event) => setMinutes(Number(event.target.value))} disabled={busy || !!active}>
          {[5, 15, 30, 60].map((value) => <option key={value} value={value}>{value}</option>)}
        </select></label>
        {scope === "document" && <label>{t("diagnostics.documentId")} <input value={docId} onChange={(event) => setDocId(event.target.value)} maxLength={32} disabled={busy || !!active} /></label>}
        <button type="button" className="btn" disabled={busy || !actions.canStart || scope === "document" && !/^[a-f0-9]{16,32}$/.test(docId)}
          onClick={() => action(() => startDiagnosticSession({ scope, minutes, ...(scope === "document" ? { doc_id: docId } : {}) }))}>{t("diagnostics.start")}</button>
        <button type="button" className="btn ghost" disabled={busy || !actions.canStop}
          onClick={() => action(() => stopDiagnosticSession(active.id))}>{t("diagnostics.stop")}</button>
      </div>
      {active && ["system", "interface"].includes(active.scope) && <div>
        <button type="button" className="btn ghost" disabled={busy} onClick={() => action(async () => {
          const result = await inviteDiagnosticBrowser(active.id);
          setInvitation(result.code);
        })}>{t("diagnostics.createInvitation")}</button>
        {invitation && <p>{t("diagnostics.invitationOnce")}: <code>{invitation}</code></p>}
      </div>}
    </section>

    <section className="panel diagnostics-card" aria-label={t("diagnostics.eventsTitle")}>
      <h2>{t("diagnostics.eventsTitle")}</h2>
      <div className="diagnostics-controls">
        <label>{t("diagnostics.from")} <input type="datetime-local" value={from} onChange={(event) => { setFrom(event.target.value); setEvents(null); }} /></label>
        <label>{t("diagnostics.to")} <input type="datetime-local" value={to} onChange={(event) => { setTo(event.target.value); setEvents(null); }} /></label>
        <label>{t("diagnostics.requestId")} <input value={requestId} onChange={(event) => { setRequestId(event.target.value); setEvents(null); }} maxLength={36} /></label>
        <button type="button" className="btn" disabled={busy || stale} onClick={() => action(async () => {
          const query = { ...filters(), to_utc: to ? new Date(to).toISOString() : status.server_now, limit: 100 };
          const result = await queryDiagnosticEvents(query);
          setEvents({ ...result, query });
        })}>{t("diagnostics.refreshEvents")}</button>
      </div>
      {events?.partial && <p role="alert">{t("diagnostics.partialPeriod")}</p>}
      {events && (events.events.length ? <ul className="diagnostics-events">
        {[...events.events].sort((a, b) => b.timestamp_utc.localeCompare(a.timestamp_utc)).map((event) => <li key={event.event_id}>
          <time>{date(event.timestamp_utc)}</time> · {event.component} · {event.event_code}
          {event.error_code && <> · {event.error_code}</>}
          {event.request_id && <> · <code>{event.request_id}</code></>}
        </li>)}
      </ul> : <p>{t("diagnostics.noEvents")}</p>)}
      {events?.next_offset != null && <button type="button" className="btn ghost" disabled={busy}
        onClick={() => action(async () => {
          const currentQuery = events.query;
          const next = await queryDiagnosticEvents({ ...currentQuery, offset: events.next_offset });
          setEvents((current) => current?.query === currentQuery ? {
            ...current, events: [...current.events, ...next.events], next_offset: next.next_offset,
            partial: current.partial || next.partial,
          } : current);
        })}>{t("diagnostics.moreEvents")}</button>}
    </section>

    <section className="panel diagnostics-card" aria-label={t("diagnostics.bundlesTitle")}>
      <h2>{t("diagnostics.bundlesTitle")}</h2>
      <div className="diagnostics-controls">
        <label>{t("diagnostics.bundleSessionId")} <input value={bundleSessionId} onChange={(event) => setBundleSessionId(event.target.value.trim())}
          maxLength={36} list="diagnostic-session-choices" /></label>
        <datalist id="diagnostic-session-choices">
          {active?.id && <option value={active.id}>{t("diagnostics.bundleActiveSession")}</option>}
          {status?.session?.last_session?.id && <option value={status.session.last_session.id}>{t("diagnostics.bundleLastSession")}</option>}
        </datalist>
        <button type="button" className="btn" disabled={busy || !actions.canBundle}
          onClick={() => action(() => createDiagnosticBundle(diagnosticBundleRequest(filters(), bundleSessionId)), { refreshBundles: true })}>{t("diagnostics.createBundle")}</button>
        <button type="button" className="btn ghost" disabled={busy} onClick={() => action(loadBundles)}>{t("diagnostics.refreshBundles")}</button>
      </div>
      {bundles.length === 0 ? <p>{t("diagnostics.noBundles")}</p> : <ul className="diagnostics-bundles">
        {bundles.map((bundle) => <li key={bundle.id}>
          <strong>{t(diagnosticStatusKey(bundle.status))}</strong> · {date(bundle.created_at)} · {mib(bundle.size_bytes)}
          {bundle.expires_at && <> · {t("diagnostics.expires")}: {date(bundle.expires_at)}</>}
          {bundle.status === "ready" && <button type="button" className="btn ghost" disabled={busy}
            onClick={() => action(async () => { const result = await previewDiagnosticBundle(bundle.id); setPreview({ id: bundle.id, ...result }); })}>{t("diagnostics.preview")}</button>}
          {bundle.status === "ready" && actions.canDownload && <a className="btn" href={diagnosticDownloadUrl(bundle.id)}>{t("diagnostics.download")}</a>}
          {!["deleted", "expired"].includes(bundle.status) && <button type="button" className="btn ghost" disabled={busy}
            onClick={() => setPendingDeleteId(bundle.id)}>{t("diagnostics.delete")}</button>}
        </li>)}
      </ul>}
      {preview && <div className="diagnostics-preview"><h3>{t("diagnostics.previewTitle")}</h3>
        <p>{preview.manifest.partial ? t("diagnostics.partialNotice") : t("diagnostics.completeNotice")}</p>
        <p>{t("diagnostics.eventCount", { count: preview.manifest.counts?.events ?? 0 })}</p>
        {preview.manifest.counts?.recorder_sampled_success > 0 &&
          <p>{t("diagnostics.sampledSuccess", { count: preview.manifest.counts.recorder_sampled_success })}</p>}
        <p>{t("diagnostics.cutoff")}: {date(preview.manifest.cutoff_at)}</p>
        {preview.manifest.gaps?.length > 0 && <p role="alert">{t("diagnostics.gaps")}: {preview.manifest.gaps.map((gap) => t(diagnosticGapKey(gap))).join(" ")}</p>}
        <h4>{t("diagnostics.coverage")}</h4>
        <ul>{Object.entries(preview.manifest.coverage_utc || {}).map(([component, range]) =>
          <li key={component}>{component}: {date(range.from_utc)} — {date(range.to_utc)}</li>)}</ul>
      </div>}
    </section>
    {pendingDeleteId && <Modal title={t("diagnostics.delete")}
      onClose={() => { if (!busy) setPendingDeleteId(null); }}
      footer={<>
        <button type="button" className="modal-btn" disabled={busy} onClick={() => setPendingDeleteId(null)}>{t("common.cancel")}</button>
        <button type="button" className="modal-btn danger" disabled={busy} onClick={() => void confirmDelete()}>{t("diagnostics.delete")}</button>
      </>}>
      <p>{t("diagnostics.confirmDelete")}</p>
    </Modal>}
  </main>;
}
