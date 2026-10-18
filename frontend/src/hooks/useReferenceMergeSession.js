"use client";

import { useEffect, useMemo, useRef } from "react";
import * as api from "@/lib/api";
import { createReferenceMergeAdapter } from "@/lib/referenceMergeLease.mjs";
import { useEditorLeaseSession } from "@/hooks/useEditorLeaseSession";
import { referenceErrorLosesAccess } from "@/lib/editorLeaseAdapters.mjs";

export function useReferenceMergeSession({resourceType, sourceId, targetId, sourceSession, enabled = true}) {
  const sourceRef = useRef(sourceSession);
  useEffect(() => {sourceRef.current = sourceSession;}, [sourceSession]);
  const borrowed = Boolean(sourceSession && sourceId);
  const adapter = useMemo(() => createReferenceMergeAdapter(api, {
    source: sourceId ? {resource_type: resourceType, resource_id: String(sourceId)} : null,
    target: {resource_type: resourceType, resource_id: String(targetId)},
    borrowSource: borrowed,
  }), [resourceType, sourceId, targetId, borrowed]);
  useEffect(() => {adapter.setSourceSessionGetter(borrowed ? () => sourceRef.current?.getEditSession() : null);}, [adapter, borrowed]);
  const sourceReady = !borrowed || sourceSession.state === "editing";
  const session = useEditorLeaseSession({adapter, enabled: enabled && sourceReady});
  return {
    ...session,
    ...(!sourceReady ? {state: sourceSession.state, status: sourceSession.status} : {}),
    acquire: sourceReady ? session.acquire : sourceSession.acquire,
    markActivity: event => {session.markActivity(event); sourceSession?.markActivity(event);},
    getEditSessions: () => {
      if (!sourceReady || !session.getEditSession()) throw Object.assign(new Error("Editor access lost"), {code: "reference_edit_lock_lost"});
      return adapter.editSessions();
    },
    handleMutationError: error => {if (referenceErrorLosesAccess(error)) session.reportError(error);},
  };
}
