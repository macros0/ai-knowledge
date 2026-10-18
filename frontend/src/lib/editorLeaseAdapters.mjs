export function createReferenceLeaseAdapter(api, resource) {
  return {
    getStatus: () => api.getReferenceEditStatus(resource),
    async acquire(body) {
      const status = await api.getReferenceEditStatus(resource);
      return status.lease_required === false ? status : api.acquireReferenceEditLock(resource, body);
    },
    renew: body => api.renewReferenceEditLock(resource, body),
    release: body => api.releaseReferenceEditLock(resource, body),
  };
}

/** Document leases keep their existing token-only API; no reference epoch is sent. */
export function createScopeLeaseAdapter(api, docId) {
  const tokenBody = body => ({edit_token: body.edit_token});
  const normalize = status => {
    if (status.state !== "editing") throw Object.assign(new Error("Document editor unavailable"), {
      code: status.state === "applying" ? "scope_change_in_progress" : "scope_edit_locked", data: status,
    });
    // Older scope servers expose a floored remaining duration. Deriving their
    // clock from that duration is conservative and independent of client skew.
    const server = status.server_now || (Number.isFinite(status.idle_remaining_seconds)
      ? new Date(Date.parse(status.idle_expires_at) - status.idle_remaining_seconds * 1000).toISOString() : null);
    if (!server) throw Object.assign(new Error("Missing server lease clock"), {code: "scope_edit_lock_lost"});
    return {...status, server_now: server};
  };
  return {
    context: (_status, token) => ({edit_token: token}),
    getStatus: async () => (await api.getProcessingScope(docId)).edit_lock || {state: "available"},
    acquire: async body => normalize(await api.acquireProcessingScopeEditLock(docId, tokenBody(body))),
    renew: async body => normalize(await api.renewProcessingScopeEditLock(docId,
      {...tokenBody(body), activity_sequence: body.activity_sequence})),
    release: body => api.releaseProcessingScopeEditLock(docId, tokenBody(body)),
  };
}

export function referenceMutationHeaders(context) {
  if (!context) throw Object.assign(new Error("Editor access lost"), { code: "reference_edit_lock_lost" });
  return context.edit_token ? { "X-Reference-Edit-Token": context.edit_token,
    "X-Reference-Edit-Epoch": String(context.lease_epoch) } : {};
}

export function referenceErrorLosesAccess(error) {
  return [0, 401, 403, 404].includes(error?.status) || ["reference_edit_lock_lost", "reference_edit_locked",
    "not_found", "locale_not_found", "forbidden", "glossary_migration_required"].includes(error?.code || error?.data?.code);
}
