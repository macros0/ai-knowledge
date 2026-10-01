import { test } from "node:test";
import assert from "node:assert/strict";
import * as sourceTree from "../src/lib/sourceTree.mjs";
import * as i18n from "../src/i18n/core.js";

test("reply metadata labels the sender and original date with its timezone", () => {
  const { t } = i18n.createTranslator("ru");
  const source = { kind: "document", metadata: {
    mail: true, sender: "sender@example.test", sent_at: "2023-09-26T18:55:58Z",
  } };
  const text = sourceTree.sourceMailMeta(source, (date) => i18n.formatMailDateTime(date, "ru", "Europe/Moscow"), t);
  assert.match(text, /Отправитель: sender@example\.test/);
  assert.match(text, /Дата письма \/ ответа: 26\.09\.2023/);
  assert.match(text, /21:55/);
  assert.match(text, /GMT\+3/);
});

test("missing reply date is explicit and ordinary documents have no mail header", () => {
  const { t } = i18n.createTranslator("ru");
  const format = () => { throw new Error("No date should be formatted"); };
  assert.equal(sourceTree.sourceMailMeta({ kind: "mail", metadata: { sent_at: "invalid" } }, format, t),
    "Дата письма / ответа: Дата неизвестна");
  assert.equal(sourceTree.sourceMailMeta({ kind: "document" }, format, t), null);
});

test("mail header follows the selected interface language", () => {
  const { t } = i18n.createTranslator("en");
  assert.equal(sourceTree.sourceMailMeta({ kind: "mail", metadata: { sender: "sender@example.test" } }, () => "", t),
    "Sender: sender@example.test · Email / reply date: Date unknown");
});
