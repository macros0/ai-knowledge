"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { approveJob, cancelJob, deleteExportArtifacts, friendlyApiError, listJobs } from "@/lib/api";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";
import { useChat } from "@/context/ChatContext";

const PENDING_STATUSES = ["queued", "running", "awaiting_approval"];

function fmtTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export default function AdminPanel() {
  const { showToast } = useToast();
  const { t } = useI18n();
  const { settings } = useChat();
  const [jobs, setJobs] = useState([]);
  const [busy, setBusy] = useState({});
  const mounted = useRef(true);

  const JOB_STATUS_LABELS = {
    queued: t("admin.jobStatus.queued"),
    running: t("admin.jobStatus.running"),
    awaiting_approval: t("admin.jobStatus.awaiting_approval"),
    completed: t("admin.jobStatus.completed"),
    failed: t("admin.jobStatus.failed"),
    cancelled: t("admin.jobStatus.cancelled"),
  };

  const JOB_TYPE_LABELS = {
    bulk_delete: t("admin.jobType.bulk_delete"),
    bulk_regenerate: t("admin.jobType.bulk_regenerate"),
    bulk_export: t("admin.jobType.bulk_export"),
  };

  const load = useCallback(async () => {
    try {
      const list = await listJobs();
      if (mounted.current) setJobs(list);
    } catch {
      // бэкенд недоступен — молча пропускаем (повторим по таймеру)
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    load();
    const timer = setInterval(load, 3000);
    return () => {
      mounted.current = false;
      clearInterval(timer);
    };
  }, [load]);

  const pending = jobs.filter((j) => PENDING_STATUSES.includes(j.status)).length;

  const act = async (id, fn) => {
    setBusy((b) => ({ ...b, [id]: true }));
    try {
      await fn(id);
      await load();
    } catch (err) {
      showToast(friendlyApiError(err, t), { type: "error" });
    } finally {
      setBusy((b) => ({ ...b, [id]: false }));
    }
  };

  return (
    <section className="panel admin-panel">
      <div className="panel-head">
        <h2>{t("admin.title")}</h2>
        <span className={`job-pending${pending > 0 ? " active" : ""}`}>
          {t("admin.pending", { count: pending })}
        </span>
      </div>

      {jobs.length === 0 ? (
        <p className="muted">{t("admin.empty")}</p>
      ) : (
        <ul className="job-list">
          {jobs.map((job) => (
            <li key={job.id} className="job-item">
              <div className="job-main">
                <span className={`job-status ${job.status}`}>
                  {JOB_STATUS_LABELS[job.status] ?? job.status}
                </span>
                <strong>{JOB_TYPE_LABELS[job.job_type] ?? job.job_type}</strong>
                <span className="job-docs">
                  {t("admin.jobDocs", { count: (job.params?.doc_ids ?? job.params?.documents ?? []).length })}
                </span>
              </div>
              <div className="job-meta">
                #{job.id} · {job.created_by ?? "—"} · {fmtTime(job.created_at)}
                {job.approved_by ? ` · ${t("admin.approvedBy", { name: job.approved_by })}` : ""}
              </div>
              {job.error && <div className="job-error">{job.result?.error_code ? t(`apiError.${job.result.error_code}`) : job.error}</div>}
              {job.result?.errors?.length > 0 && (
                <div className="job-error">
                  {t("admin.errorsByDocs", {
                    ids: job.result.errors.map((e) => e.doc_id).join(", "),
                  })}
                </div>
              )}
              {job.job_type === "bulk_export" && job.result && (
                <div className="job-meta">
                  {t("admin.exportProgress", { processed: job.result.processed ?? 0, total: job.result.total ?? 0 })}
                  {job.result.expires_at ? ` · ${t("admin.exportExpires", { time: fmtTime(job.result.expires_at) })}` : ""}
                  {job.result.artifact_status === "available" && !settings.bulk_export_download_enabled ? ` · ${t("admin.exportDownloadDisabled")}` : ""}
                  {job.result.artifact_status === "available" && settings.bulk_export_download_enabled && (job.result.parts ?? []).map((part) => (
                    <span key={part.number}> · <a href={`/api/jobs/${job.id}/export/${part.number}`}>{t("admin.exportDownload", { number: part.number })}</a></span>
                  ))}
                </div>
              )}
              <div className="job-actions">
                {job.job_type !== "bulk_export" && job.status === "awaiting_approval" && (
                  <button
                    className="modal-btn"
                    disabled={busy[job.id]}
                    onClick={() => act(job.id, approveJob)}
                  >
                    {t("admin.approve")}
                  </button>
                )}
                {job.job_type !== "bulk_export" && (job.status === "queued" || job.status === "awaiting_approval") && (
                  <button
                    className="modal-btn"
                    disabled={busy[job.id]}
                    onClick={() => act(job.id, cancelJob)}
                  >
                    {t("admin.cancel")}
                  </button>
                )}
                {job.job_type === "bulk_export" && job.result?.artifact_status === "available" && (
                  <button className="modal-btn" disabled={busy[job.id]} onClick={() => act(job.id, deleteExportArtifacts)}>
                    {t("admin.exportDelete")}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
