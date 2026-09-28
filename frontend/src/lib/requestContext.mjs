import { AsyncLocalStorage } from "node:async_hooks";
import { randomUUID } from "node:crypto";
import { validRequestId } from "./diagnosticSchema.mjs";

const contextStorage = new AsyncLocalStorage();
export const currentRequestId = () => contextStorage.getStore()?.requestId || null;
export const requestIdFromHeaders = (headers) => {
  const candidate = headers?.get?.("x-request-id");
  return validRequestId(candidate) ? candidate : currentRequestId() || randomUUID();
};
export function withRequestContext(context, callback) {
  const requestId = validRequestId(context?.requestId) ? context.requestId : randomUUID();
  return contextStorage.run(Object.freeze({ requestId }), callback);
}
