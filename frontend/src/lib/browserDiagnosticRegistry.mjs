import { DiagnosticClient } from "./diagnosticClient.mjs";

export function createBrowserDiagnosticRegistry(create = () => new DiagnosticClient({
  buildId: process.env.NEXT_PUBLIC_APP_REVISION || "unknown",
})) {
  let client = null;
  let owner = undefined;
  return {
    forUser(userId) {
      if (!client) client = create();
      if (owner !== undefined && owner !== userId) client.deactivate();
      owner = userId;
      return client;
    },
    reportRootError(error, localReportId) {
      if (!client?.report(error, { requestId: error?.requestId, localReportId })) return false;
      void client.flush().catch(() => {});
      return true;
    },
  };
}

// In-memory only. A root error replaces the React layout but keeps this JS
// realm, allowing the explicitly joined browser to send its last safe event.
export const browserDiagnosticRegistry = createBrowserDiagnosticRegistry();
