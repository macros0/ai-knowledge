export function resolveToastDuration(type, explicitDuration) {
  return explicitDuration ?? (type === "error" ? 0 : 5000);
}
