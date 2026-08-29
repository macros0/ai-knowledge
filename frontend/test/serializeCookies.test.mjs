import { test } from "node:test";
import assert from "node:assert/strict";
import { serializeCookies } from "../src/lib/serializeCookies.mjs";

test("сохраняет =, +, / в значении cookie без URL-кодирования", () => {
  // Реалистичная signed-cookie Starlette: base64 с padding и спецсимволами.
  const value =
    "eyJpZGVudGl0eSI6eyJleHRlcm5hbF9pZCI6ImtjLXN1Yi0xMjMifX0=" +
    ".apH9Ng" +
    ".9VpE9uwF3reLjeOCvU9HU3bgAfE/+==";
  const result = serializeCookies([{ name: "session", value }]);
  assert.equal(result, `session=${value}`);
  assert.ok(!result.includes("%"), "значение не должно быть URL-кодировано");
  assert.ok(!result.includes("%3D"), "нет %3D");
  assert.ok(!result.includes("%2B"), "нет %2B");
  assert.ok(!result.includes("%2F"), "нет %2F");
});

test("несколько cookie объединяются через '; '", () => {
  assert.equal(
    serializeCookies([
      { name: "a", value: "1" },
      { name: "b", value: "2" },
    ]),
    "a=1; b=2"
  );
});

test("пустой список -> пустая строка", () => {
  assert.equal(serializeCookies([]), "");
});
