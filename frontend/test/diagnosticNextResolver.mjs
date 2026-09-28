export function resolve(specifier, context, nextResolve) {
  return nextResolve(["next/headers", "next/server"].includes(specifier) ? `${specifier}.js` : specifier, context);
}
