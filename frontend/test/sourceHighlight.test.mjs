import test from "node:test";
import assert from "node:assert/strict";
import { sourceSpansToLineRanges } from "../src/lib/sourceHighlight.mjs";
import * as sourceHighlight from "../src/lib/sourceHighlight.mjs";

test("a table row span ending in a newline does not highlight the next row", () => {
  const text = "| 1 |\n| 2 |\n| 3 |";
  const start = text.indexOf("| 2 |");
  const end = text.indexOf("\n", start) + 1;

  assert.deepEqual(sourceSpansToLineRanges(text, [{ start, end }]), [[2, 2]]);
});

test("a span crossing into another line highlights both lines", () => {
  const text = "строка 1\nначало\nконец";
  const start = text.indexOf("начало");
  const end = text.indexOf("конец") + "конец".length;

  assert.deepEqual(sourceSpansToLineRanges(text, [{ start, end }]), [[2, 3]]);
});

test("raw highlight keeps Python character offsets after emoji", () => {
  assert.deepEqual(
    sourceHighlight.splitRawSourceSpans("😀ABC", [{ start: 1, end: 2 }]),
    [
      { text: "😀", spanIndex: null },
      { text: "A", spanIndex: 0 },
      { text: "BC", spanIndex: null },
    ],
  );
});

test("raw highlight keeps separate evidence ranges navigable", () => {
  assert.deepEqual(
    sourceHighlight.splitRawSourceSpans("a b c", [{ start: 0, end: 1 }, { start: 4, end: 5 }]),
    [
      { text: "a", spanIndex: 0 },
      { text: " b ", spanIndex: null },
      { text: "c", spanIndex: 1 },
    ],
  );
});
