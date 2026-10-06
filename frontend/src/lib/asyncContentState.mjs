export function resolveAsyncContentState({ pending=false, hasLoaded=false, hasData=false, hasFilters=false, error=null }={}) {
  if (pending) return hasData ? "refreshing" : "loading";
  if (error) return hasData ? "stale-error" : "error";
  if (hasData) return "ready";
  if (!hasLoaded) return "loading";
  return hasFilters ? "filtered-empty" : "empty";
}
