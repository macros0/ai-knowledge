import { friendlyApiError } from "./api.js";
import { validRequestId } from "./diagnosticIdentifiers.mjs";

export function apiToast(error, translate, options = {}) {
  const requestId = validRequestId(error?.requestId) ? error.requestId : null;
  const localReportId = !requestId && validRequestId(error?.localReportId)
    ? error.localReportId : null;
  return {
    message: friendlyApiError(error, translate),
    options: { ...options, requestId, localReportId },
  };
}
