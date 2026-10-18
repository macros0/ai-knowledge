"use client";

import { useMemo } from "react";
import * as api from "@/lib/api";
import { createReferenceLeaseAdapter, referenceMutationHeaders, referenceErrorLosesAccess } from "@/lib/editorLeaseAdapters.mjs";
import { useEditorLeaseSession } from "@/hooks/useEditorLeaseSession";

export function useReferenceEditSession({ resource, enabled, onLost, onAcquired }) {
  const identity = JSON.stringify(resource || null);
  const adapter = useMemo(() => createReferenceLeaseAdapter(api, JSON.parse(identity)), [identity]);
  const session = useEditorLeaseSession({adapter, enabled, onLost, onAcquired});
  return {...session, getMutationHeaders: () => referenceMutationHeaders(session.getEditSession()),
    handleMutationError: error => {if (referenceErrorLosesAccess(error)) session.reportError(error);}};
}
