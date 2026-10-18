import test from "node:test";
import assert from "node:assert/strict";
import { createEditorIdleTimer } from "../src/lib/editorIdle.mjs";

import { clock } from "./support/clock.mjs";

test("idle warning begins at nine minutes and activity extends the deadline", () => {
  const time = clock(), warnings = [], activities = [];
  let expired = 0;
  const timer = createEditorIdleTimer({ ...time, onCountdown: value => warnings.push(value),
    onActivity: sequence => activities.push(sequence), onExpire: () => expired++ });
  time.advance(540000);
  assert.equal(warnings.at(-1), 60);
  time.advance(59000); assert.equal(expired, 0);
  timer.activity(); assert.deepEqual(activities, [1]);
  time.advance(600000); assert.equal(expired, 1);
  assert.equal(timer.activity(), false);
  assert.equal(time.pending.size, 0);
});

test("stopping an old timer cannot expire a newly opened editor", () => {
  const time = clock(); let old = 0, fresh = 0;
  const first = createEditorIdleTimer({ ...time, onCountdown() {}, onExpire: () => old++ });
  time.advance(500000); first.stop();
  createEditorIdleTimer({ ...time, onCountdown() {}, onExpire: () => fresh++ });
  time.advance(100000); assert.equal(old, 0); assert.equal(fresh, 0);
  time.advance(500000); assert.equal(fresh, 1);
});
