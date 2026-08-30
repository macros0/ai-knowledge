"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { approveJob, cancelJob, listJobs } from "@/lib/api";
import { useToast } from "./Toast";

const JOB_STATUS_LABELS = {
  queued: "В очереди",
  running: "Выполняется",
  awaiting_approval: "Требует одобрения",
  completed: "Завершена",
  failed: "Ошибка",
  cancelled: "Отменена",
};

const JOB_TYPE_LABELS = {
  bulk_delete: "Массовое удаление",
  bulk_regenerate: "Массовая перегенерация",
};

const PENDING_STATUSES = ["queued", "running", "awaiting_approval"];

function fmtTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export default function AdminPanel() {
  const { showToast } = useToast();
  const [jobs, setJobs] = useState([]);
  const [busy, setBusy] = useState({});
  const mounted = useRef(true);

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
    const t = setInterval(load, 3000);
    return () => {
      mounted.current = false;
      clearInterval(t);
    };
  }, [load]);

  const pending = jobs.filter((j) => PENDING_STATUSES.includes(j.status)).length;

  const act = async (id, fn) => {
    setBusy((b) => ({ ...b, [id]: true }));
    try {
      await fn(id);
      await load();
    } catch (err) {
      showToast(err.message, { type: "error" });
    } finally {
      setBusy((b) => ({ ...b, [id]: false }));
    }
  };

  return (
    <section className="panel admin-panel">
      <div className="panel-head">
        <h2>Системные операции</h2>
        <span className={`job-pending${pending > 0 ? " active" : ""}`}>
          Активных задач: {pending}
        </span>
      </div>

      {jobs.length === 0 ? (
        <p className="muted">Массовых операций ещё не было.</p>
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
                  документов: {(job.params?.doc_ids ?? []).length}
                </span>
              </div>
              <div className="job-meta">
                #{job.id} · {job.created_by ?? "—"} · {fmtTime(job.created_at)}
                {job.approved_by ? ` · одобрил: ${job.approved_by}` : ""}
              </div>
              {job.error && <div className="job-error">{job.error}</div>}
              {job.result?.errors?.length > 0 && (
                <div className="job-error">
                  Ошибки по документам:{" "}
                  {job.result.errors.map((e) => e.doc_id).join(", ")}
                </div>
              )}
              <div className="job-actions">
                {job.status === "awaiting_approval" && (
                  <button
                    className="modal-btn"
                    disabled={busy[job.id]}
                    onClick={() => act(job.id, approveJob)}
                  >
                    Одобрить
                  </button>
                )}
                {(job.status === "queued" || job.status === "awaiting_approval") && (
                  <button
                    className="modal-btn"
                    disabled={busy[job.id]}
                    onClick={() => act(job.id, cancelJob)}
                  >
                    Отменить
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
