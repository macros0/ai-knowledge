/** Atomic acquisition for a merge; an existing source editor keeps its own lease. */
export function createReferenceMergeAdapter(api, {source, target, getSourceSession, borrowSource = false}) {
  const resources = [source, target].filter(Boolean);
  const borrowing = Boolean(getSourceSession || borrowSource);
  const owned = resources.filter(resource => !source || !borrowing || resource !== source);
  let getBorrowed = getSourceSession;
  let acquired = [], token = null, borrowed = null, disabled = false;
  const lost = () => Object.assign(new Error("Editor access lost"), {code: "reference_edit_lock_lost"});
  const aggregate = statuses => {
    const last = statuses.at(-1);
    if (disabled) return {...last, lease_required: false};
    return {...last, expires_at: new Date(Math.min(...statuses.map(row => Date.parse(row.expires_at)))).toISOString(),
      idle_expires_at: new Date(Math.min(...statuses.map(row => Date.parse(row.idle_expires_at)))).toISOString()};
  };
  const context = item => ({edit_token: token, lease_epoch: item.status.lease_epoch});
  const releaseOwned = async () => {
    const previous = acquired; acquired = [];
    await Promise.allSettled(previous.filter(item => Number.isInteger(item.status?.lease_epoch))
      .map(item => api.releaseReferenceEditLock(item.resource, context(item))));
  };
  return {
    setSourceSessionGetter: getter => {getBorrowed = getter;},
    async getStatus() {
      const statuses = await Promise.all(owned.map(api.getReferenceEditStatus));
      return statuses.find(row => row.state === "editing") || statuses.at(-1);
    },
    async acquire(body) {
      const before = await Promise.all(resources.map(api.getReferenceEditStatus));
      disabled = before.every(row => row.lease_required === false);
      if (disabled) return aggregate(before);
      token = body.edit_token;
      borrowed = source && borrowing ? getBorrowed?.() : null;
      if (source && borrowing && !borrowed?.edit_token) throw lost();
      const response = await api.acquireReferenceEditLocks(resources.map(resource => ({...resource,
        edit_token: resource === source && borrowed ? borrowed.edit_token : token,
        ...(resource === source && borrowed ? {lease_epoch: borrowed.lease_epoch} : {})})));
      acquired = owned.map(resource => ({resource, status: response.resources?.find(item =>
        item.resource_type === resource.resource_type && String(item.resource_id) === resource.resource_id)}));
      try {
        if (acquired.some(item => !item.status)) throw lost();
        return aggregate(acquired.map(item => item.status));
      } catch (error) {await releaseOwned(); throw error;}
    },
    async renew(body) {
      await Promise.all(acquired.map(async item => {
        item.status = await api.renewReferenceEditLock(item.resource, {...context(item), activity_sequence: body.activity_sequence});
      }));
      return aggregate(acquired.map(item => item.status));
    },
    async release() {
      await releaseOwned();
    },
    editSessions() {
      if (disabled) return {};
      const sessions = Object.fromEntries(acquired.map(item => [item.resource.resource_id, context(item)]));
      if (borrowed) {
        const current = getBorrowed?.();
        if (!current || current.edit_token !== borrowed.edit_token || current.lease_epoch !== borrowed.lease_epoch) throw lost();
        sessions[source.resource_id] = current;
      }
      return sessions;
    },
  };
}
