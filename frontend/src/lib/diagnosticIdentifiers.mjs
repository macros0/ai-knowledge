// Browser-safe correlation validation. IDs are labels, never access tokens.
export const validRequestId = (value) => typeof value === "string"
  && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value);
export const errorReference = (header, body) => validRequestId(header) ? header : validRequestId(body) ? body : null;
