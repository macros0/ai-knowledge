"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { useI18n } from "@/i18n/LocaleContext";
import { browserDiagnosticRegistry } from "@/lib/browserDiagnosticRegistry.mjs";
import { getDiagnosticBrowserStatus, joinDiagnosticBrowser, leaveDiagnosticBrowser, friendlyApiError } from "@/lib/api";
import ErrorReference from "./ErrorReference";

export default function DiagnosticClientReporter() {
  const { user, hasRole } = useAuth();
  const { t } = useI18n();
  const reporter = useRef(null);
  const [code, setCode] = useState("");
  const [active, setActive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [availableFor, setAvailableFor] = useState(null);
  const generation = useRef(0);

  useEffect(() => {
    const instance = browserDiagnosticRegistry.forUser(user?.user_id || null);
    reporter.current = instance;
    setActive(instance.status().active); setCode(""); setError(null);
    return () => { generation.current++; reporter.current = null; };
  }, [user?.user_id]);

  useEffect(() => {
    const userId = user?.user_id;
    if (!userId || userId === "anonymous") return;
    let disposed = false;
    let checking = false;
    const refresh = async () => {
      if (checking || document.visibilityState !== "visible") return;
      checking = true;
      let available = false;
      try { available = (await getDiagnosticBrowserStatus()).available === true; }
      catch { /* Hide connection controls unless the server confirms availability. */ }
      finally { checking = false; }
      if (disposed) return;
      setAvailableFor(available ? userId : null);
      if (!available) {
        generation.current++;
        reporter.current?.deactivate();
        setActive(false); setBusy(false); setCode(""); setError(null);
      }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 10000);
    document.addEventListener("visibilitychange", refresh);
    window.addEventListener("focus", refresh);
    return () => {
      disposed = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
      window.removeEventListener("focus", refresh);
    };
  }, [user?.user_id]);

  useEffect(() => {
    if (!active || !reporter.current) return;
    const instance = reporter.current;
    const report = (event) => {
      const error = event.error || event.reason;
      instance.report(error, { requestId: error?.requestId, localReportId: error?.localReportId });
    };
    window.addEventListener("error", report);
    window.addEventListener("unhandledrejection", report);
    const boundary = (event) => instance.report(event.detail?.error, { localReportId: event.detail?.localReportId });
    window.addEventListener("okf-diagnostic-boundary-error", boundary);
    const timer = setInterval(async () => {
      await instance.flush();
      if (!instance.status().active) setActive(false);
    }, 6000);
    return () => { clearInterval(timer); window.removeEventListener("error", report); window.removeEventListener("unhandledrejection", report); window.removeEventListener("okf-diagnostic-boundary-error", boundary); };
  }, [active]);

  const join = useCallback(async (own = false) => {
    const epoch = ++generation.current;
    setBusy(true); setError(null);
    try {
      const membership = await joinDiagnosticBrowser(own ? null : code.trim());
      if (generation.current === epoch && reporter.current) setActive(reporter.current.activate(membership));
      setCode("");
    } catch (error) { if (generation.current === epoch) setError(error); }
    finally { if (generation.current === epoch) setBusy(false); }
  }, [code]);

  async function leave() {
    const participationId = reporter.current?.participationId();
    generation.current++; reporter.current?.deactivate(); setActive(false); setBusy(true); setError(null);
    try { if (participationId) await leaveDiagnosticBrowser(participationId); }
    catch (error) { setError(error); }
    finally { setBusy(false); }
  }

  if (!user || user.user_id === "anonymous" || availableFor !== user.user_id) return null;
  return <details className="diagnostic-browser-controls">
    <summary>{t("diagnostics.browserTitle")}{active ? ` · ${t("diagnostics.browserActive")}` : ""}</summary>
    <p>{t("diagnostics.browserHelp")}</p>
    {active ? <button type="button" className="btn ghost" disabled={busy} onClick={leave}>{t("diagnostics.browserLeave")}</button>
      : <form onSubmit={(event) => { event.preventDefault(); void join(); }}>
        <label>{t("diagnostics.invitationCode")} <input value={code} onChange={(event) => setCode(event.target.value)} maxLength={22} autoComplete="off" spellCheck={false} /></label>{" "}
        <button type="submit" className="btn" disabled={busy || !/^[A-Za-z0-9_-]{22}$/.test(code.trim())}>{t("diagnostics.browserJoin")}</button>{" "}
        {hasRole("admin") && <button type="button" className="btn ghost" disabled={busy} onClick={() => join(true)}>{t("diagnostics.browserOwn")}</button>}
      </form>}
    {error && <p role="alert">{friendlyApiError(error, t)}</p>}
    {error && <ErrorReference requestId={error.requestId} localReportId={error.localReportId} />}
  </details>;
}
