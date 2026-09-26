import { friendlyApiError } from "./api.js";

export function uploadFailureMessage(filename, error, t) {
  return t("upload.failedReason", {
    file: filename,
    reason: friendlyApiError(error, t),
  });
}
