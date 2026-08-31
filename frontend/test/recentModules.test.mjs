import { test } from "node:test";
import assert from "node:assert/strict";
import {
  RECENT_MODULES_MAX,
  recentModulesKey,
  readRecentModules,
  recordRecentModule,
  pruneRecentModules,
} from "../src/lib/recentModules.mjs";

function makeStorage(initial = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
    data,
  };
}

test("record двигает модуль в начало и дедуплицирует", () => {
  const storage = makeStorage();
  recordRecentModule(storage, "alice", "PY");
  recordRecentModule(storage, "alice", "OM");
  recordRecentModule(storage, "alice", "PY");
  assert.deepEqual(readRecentModules(storage, "alice"), ["PY", "OM"]);
});

test("record ограничивает ряд капом RECENT_MODULES_MAX", () => {
  const storage = makeStorage();
  for (const m of ["A", "B", "C", "D", "E", "F", "G"]) {
    recordRecentModule(storage, "alice", m);
  }
  const recents = readRecentModules(storage, "alice");
  assert.equal(recents.length, RECENT_MODULES_MAX);
  assert.deepEqual(recents, ["G", "F", "E", "D", "C", "B"]);
});

test("read возвращает пустой список при отсутствии ключа", () => {
  assert.deepEqual(readRecentModules(makeStorage(), "alice"), []);
});

test("read откатывается к пустому списку при невалидном JSON", () => {
  const storage = makeStorage({ [recentModulesKey("alice")]: "{not-json" });
  assert.deepEqual(readRecentModules(storage, "alice"), []);
});

test("read фильтрует не-строковые элементы", () => {
  const storage = makeStorage({
    [recentModulesKey("alice")]: JSON.stringify(["PY", 42, null, "OM"]),
  });
  assert.deepEqual(readRecentModules(storage, "alice"), ["PY", "OM"]);
});

test("ключ персональный: у разных пользователей разные списки", () => {
  const storage = makeStorage();
  recordRecentModule(storage, "alice", "PY");
  recordRecentModule(storage, "bob", "OM");
  assert.deepEqual(readRecentModules(storage, "alice"), ["PY"]);
  assert.deepEqual(readRecentModules(storage, "bob"), ["OM"]);
});

test("prune удаляет модули, которых нет в справочнике, и персистит", () => {
  const storage = makeStorage();
  recordRecentModule(storage, "alice", "PY");
  recordRecentModule(storage, "alice", "OM");
  recordRecentModule(storage, "alice", "PA");
  const next = pruneRecentModules(storage, "alice", ["PY", "PA"]);
  assert.deepEqual(next, ["PA", "PY"]);
  assert.deepEqual(readRecentModules(storage, "alice"), ["PA", "PY"]);
});

test("read не бросает при storage.getItem, кидающем исключение", () => {
  const throwing = {
    getItem: () => {
      throw new Error("quota");
    },
    setItem: () => {},
  };
  assert.deepEqual(readRecentModules(throwing, "alice"), []);
  assert.deepEqual(recordRecentModule(throwing, "alice", "PY"), ["PY"]);
});
