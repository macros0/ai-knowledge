export function serializeCookies(cookies) {
  return cookies.map((c) => `${c.name}=${c.value}`).join("; ");
}
