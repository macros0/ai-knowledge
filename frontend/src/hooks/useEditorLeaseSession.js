"use client";

import { useEffect, useRef, useState } from "react";
import { createEditorLeaseSession } from "@/lib/editorLeaseSession.mjs";

export function useEditorLeaseSession({ adapter, enabled, onLost, onExpire, onAcquired }) {
  const controller = useRef(null), callback = useRef(onLost);
  const expireCallback = useRef(onExpire);
  const acquiredCallback = useRef(onAcquired);
  const [retry, setRetry] = useState(0);
  const [snapshot, setSnapshot] = useState({ state: "viewing", status: null, countdown: null });
  useEffect(() => { callback.current = onLost; }, [onLost]);
  useEffect(() => { expireCallback.current = onExpire; }, [onExpire]);
  useEffect(() => { acquiredCallback.current = onAcquired; }, [onAcquired]);
  useEffect(() => {
    if (!enabled) return;
    const session = createEditorLeaseSession({ adapter, onChange: setSnapshot,
      onAcquired: (status, isCurrent) => acquiredCallback.current?.(status, isCurrent),
      onLost: error => callback.current?.(error), onExpire: () => {
        if (expireCallback.current) expireCallback.current();
        else callback.current?.({code: "reference_edit_lock_lost"});
      } });
    controller.current = session;
    // Strict Mode may clean up and replay the effect in this turn. A disposed
    // controller must not send an acquire that competes with its replacement.
    Promise.resolve().then(() => session.start());
    const onVisibility = () => {
      if (document.hidden || session.snapshot().state !== "editing") return;
      setSnapshot(previous => ({ ...previous, state: "acquiring" }));
      session.renewNow().finally(() => { if (controller.current === session) setSnapshot(session.snapshot()); });
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => { document.removeEventListener("visibilitychange", onVisibility);
      session.dispose(); if (controller.current === session) controller.current = null; };
  }, [adapter, enabled, retry]);
  useEffect(() => {
    if (!enabled || snapshot.state !== "blocked") return;
    let stopped = false, pending = false;
    const timer = setInterval(async () => {
      if (document.hidden || pending) return;
      pending = true;
      try { const status = await adapter.getStatus();
        if (!stopped) setSnapshot(previous => ({ ...previous, status,
          state: status.state === "available" ? "available" : "blocked" }));
      } catch { /* Keep viewing; never assume absence of a lock after a network error. */ }
      finally { pending = false; }
    }, 30000);
    return () => { stopped = true; clearInterval(timer); };
  }, [adapter, enabled, snapshot.state]);
  return {
    ...(enabled ? snapshot : { state: "viewing", status: null, countdown: null }),
    markActivity: event => controller.current?.activity(event),
    getEditSession: () => controller.current?.mutationContext(),
    reportError: error => controller.current?.reportError(error),
    acquire: () => setRetry(value => value + 1),
    release: () => controller.current?.dispose(),
  };
}
