import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
import { setImmediate } from "node:timers/promises";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { transformSync } from "next/dist/build/swc/index.js";
import { createBrowserDiagnosticRegistry } from "../src/lib/browserDiagnosticRegistry.mjs";
import { DiagnosticClient } from "../src/lib/diagnosticClient.mjs";
import { createTranslator } from "../src/i18n/core.js";
import ru from "../src/i18n/locales/ru.js";

const require = createRequire(import.meta.url);
const translate = createTranslator("ru", ru).t;
const compiled = transformSync(readFileSync(new URL("../src/components/DiagnosticClientReporter.jsx", import.meta.url), "utf8"), {
  filename: "DiagnosticClientReporter.jsx",
  jsc: { parser: { syntax: "ecmascript", jsx: true }, target: "es2020",
    transform: { react: { runtime: "automatic" } } }, module: { type: "commonjs" },
}).code;

// Drive the real component's effects with controlled auth/network/timer boundaries.
// The registry and browser reporter are real; no application server is required.
function mountReporter(fetchStatus, initialUser = { user_id: "reader", roles: ["viewer"] }) {
  const slots = [], effects = [], timers = new Map(), listeners = new Map();
  const registry = createBrowserDiagnosticRegistry(() => new DiagnosticClient());
  let cursor = 0, user = initialUser, nextTimer = 0;
  const hooks = {
    ...React,
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial;
      return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value; }];
    },
    useRef(initial) { const index = cursor++; return slots[index] ??= { current: initial }; },
    useCallback(callback) { return callback; },
    useEffect(callback, deps) {
      const index = cursor++, previous = slots[index];
      if (previous && deps.every((value, i) => Object.is(value, previous.deps[i]))) return;
      previous?.cleanup?.();
      slots[index] = { deps };
      effects.push(() => { slots[index].cleanup = callback(); });
    },
  };
  const events = {
    addEventListener(name, callback) { listeners.set(name, callback); },
    removeEventListener(name, callback) { if (listeners.get(name) === callback) listeners.delete(name); },
  };
  const document = { ...events, visibilityState: "visible" };
  const compiledModule = { exports: {} };
  runInNewContext(compiled, {
    module: compiledModule, exports: compiledModule.exports, document, window: events,
    setInterval(callback) { const id = ++nextTimer; timers.set(id, callback); return id; },
    clearInterval(id) { timers.delete(id); },
    require(id) {
      if (id === "react") return hooks;
      if (id === "@/context/AuthContext") return { useAuth: () => ({ user, hasRole: (role) => user?.roles.includes(role) }) };
      if (id === "@/i18n/LocaleContext") return { useI18n: () => ({ t: translate }) };
      if (id === "@/lib/browserDiagnosticRegistry.mjs") return { browserDiagnosticRegistry: registry };
      if (id === "@/lib/api") return { getDiagnosticBrowserStatus: fetchStatus };
      if (id === "./ErrorReference") return () => null;
      return require(id);
    },
  });
  function render() {
    cursor = 0;
    const html = renderToStaticMarkup(React.createElement(compiledModule.exports.default));
    while (effects.length) effects.shift()();
    return html;
  }
  return { render, registry, document,
    setUser(value) { user = value; },
    async tick() { for (const callback of [...timers.values()]) await callback(); await setImmediate(); },
    dispose() { for (const slot of slots) slot?.cleanup?.(); },
  };
}

test("connection controls are hidden until availability is confirmed, then hide when capture stops", async (t) => {
  let resolve, available = true;
  const mounted = mountReporter(() => resolve ? Promise.resolve({ available })
    : new Promise((done) => { resolve = done; }));
  t.after(() => mounted.dispose());
  assert.equal(mounted.render(), "");
  resolve({ available: true });
  await setImmediate();
  const html = mounted.render();
  assert.ok(html.includes(translate("diagnostics.browserTitle")));
  assert.ok(html.includes(translate("diagnostics.invitationCode")));
  assert.ok(!html.includes(translate("diagnostics.browserOwn")));
  available = false;
  await mounted.tick();
  assert.equal(mounted.render(), "");
});

test("stopping capture hides the connected browser and revokes its local reporting", async (t) => {
  let available = true;
  const user = { user_id: "admin", roles: ["admin"] };
  const mounted = mountReporter(async () => ({ available }), user);
  t.after(() => mounted.dispose());
  const reporter = mounted.registry.forUser(user.user_id);
  reporter.activate({ participation_id: "12345678-1234-4234-8234-123456789abc",
    expires_at: new Date(Date.now() + 300000).toISOString() });
  mounted.render();
  await setImmediate();
  assert.ok(mounted.render().includes(translate("diagnostics.browserLeave")));
  available = false;
  await mounted.tick();
  assert.equal(mounted.render(), "");
  assert.equal(reporter.status().active, false);
  assert.equal(reporter.report(new Error("synthetic")), false);
});

test("unavailable status and anonymous users never see connection controls", async (t) => {
  let calls = 0;
  const mounted = mountReporter(async () => { calls++; throw new Error("offline"); });
  t.after(() => mounted.dispose());
  assert.equal(mounted.render(), "");
  await setImmediate();
  assert.equal(mounted.render(), "");
  assert.equal(calls, 1);
  mounted.setUser({ user_id: "anonymous", roles: [] });
  assert.equal(mounted.render(), "");
  await mounted.tick();
  assert.equal(calls, 1);
});

test("a late status response cannot show controls for a different signed-in user", async (t) => {
  let resolve;
  const mounted = mountReporter(() => new Promise((done) => { resolve = done; }));
  t.after(() => mounted.dispose());
  mounted.render();
  const previousResponse = resolve;
  mounted.setUser({ user_id: "other", roles: ["viewer"] });
  assert.equal(mounted.render(), "");
  previousResponse({ available: true });
  await setImmediate();
  assert.equal(mounted.render(), "");
  resolve({ available: true });
  await setImmediate();
  assert.ok(mounted.render().includes(translate("diagnostics.browserTitle")));
});
