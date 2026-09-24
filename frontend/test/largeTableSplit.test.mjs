import { test } from "node:test";
import assert from "node:assert/strict";
import { splitLargeTables, LARGE_TABLE_MAX_ROWS } from "../src/lib/largeTableSplit.mjs";

function table(cols, dataRows) {
  const header = Array.from({ length: cols }, (_, i) => `C${i}`).join(" | ");
  const sep = Array.from({ length: cols }, () => "---").join(" | ");
  const rows = [];
  for (let r = 0; r < dataRows; r++) {
    rows.push(Array.from({ length: cols }, (_, i) => `v${r}${i}`).join(" | "));
  }
  return ["| " + header + " |", "| " + sep + " |", ...rows.map((x) => "| " + x + " |")].join("\n");
}

test("без таблиц — одна markdown-часть", () => {
  const md = "Привет\n\nВторой абзац";
  assert.deepEqual(splitLargeTables(md, 3), [{ type: "md", text: md, lineMap: [1, 2, 3] }]);
});

test("маленькая таблица (строк <= cap) не режется", () => {
  const md = "Текст до\n\n" + table(3, 2) + "\n\nТекст после";
  const parts = splitLargeTables(md, 3);
  assert.equal(parts.length, 1);
  assert.equal(parts[0].type, "md");
  assert.equal(parts[0].text, md);
});

test("гигантская таблица режется до шапки + cap строк, хвост — в remainder", () => {
  const md = "До\n\n" + table(3, 5) + "\n\nПосле";
  const parts = splitLargeTables(md, 3);

  assert.deepEqual(
    parts.map((p) => p.type),
    ["md", "md", "tableRemainder", "md"]
  );

  // часть до таблицы и после — без потерь (пробельные хвосты remark сам отрезает)
  assert.equal(parts[0].text.trim(), "До");
  assert.equal(parts[3].text.trim(), "После");

  // cap-часть содержит шапку, разделитель и ровно первые 3 строки
  const kept = parts[1].text;
  assert.equal(parts[1].tablePreview, true);
  assert.match(kept, /C0 \| C1 \| C2/);
  assert.match(kept, /--- \| --- \| ---/);
  assert.match(kept, /v00 \| v01 \| v02/);
  assert.match(kept, /v20 \| v21 \| v22/);
  assert.doesNotMatch(kept, /v30/);
  assert.doesNotMatch(kept, /v40/);

  const rem = parts[2];
  assert.deepEqual(parts[1].lineMap, [3, 4, 5, 6, 7]);
  assert.deepEqual(rem.chunkLineMaps, [[3, 4, 8, 9]]);
  assert.equal(rem.shownRows, 3);
  assert.equal(rem.remainingRows, 2);
  assert.equal(rem.totalRows, 5);
  assert.equal(rem.chunks.length, 1);
  assert.match(rem.chunks[0], /C0 \| C1 \| C2/); // повторённая шапка
  assert.match(rem.chunks[0], /--- \| --- \| ---/);
  assert.match(rem.chunks[0], /v30 \| v31 \| v32/);
  assert.match(rem.chunks[0], /v40 \| v41 \| v42/);
  // инвариант: число строк в кнопке == фактическим строкам хвоста (минус шапки)
  assert.equal(rem.remainingRows, rem.chunks.reduce((s, c) => s + c.split("\n").length - 2, 0));
  assert.equal(rem.totalRows, rem.shownRows + rem.remainingRows);
});

test("две гигантские таблицы — чередование md/remainder", () => {
  const md = "Вступление\n\n" + table(2, 6) + "\n\nСередина\n\n" + table(2, 4) + "\n\nФинал";
  const parts = splitLargeTables(md, 3);
  assert.deepEqual(
    parts.map((p) => p.type),
    ["md", "md", "tableRemainder", "md", "md", "tableRemainder", "md"]
  );
  assert.equal(parts[2].remainingRows, 3); // 6 - 3
  assert.equal(parts[5].remainingRows, 1); // 4 - 3
});

test("хвост нарезается на чанки по cap с повторённой шапкой и сохраняет порядок", () => {
  const md = table(2, 7); // 7 данных строк, cap=3 → показано 3, хвост 4 → 2 чанка
  const parts = splitLargeTables(md, 3);
  assert.deepEqual(parts.map((p) => p.type), ["md", "tableRemainder"]);

  const rem = parts[1];
  assert.equal(rem.shownRows, 3);
  assert.equal(rem.remainingRows, 4);
  assert.equal(rem.chunks.length, 2);
  assert.deepEqual(rem.chunkLineMaps, [
    [1, 2, 6, 7, 8],
    [1, 2, 9],
  ]);

  // чанк 0: шапка + разделитель + строки v30..v50
  const c0 = rem.chunks[0].split("\n");
  assert.equal(c0.length, 5);
  assert.match(c0[0], /C0 \| C1/);
  assert.match(c0[1], /--- \| ---/);
  assert.match(c0[2], /v30/);
  assert.match(c0[3], /v40/);
  assert.match(c0[4], /v50/);

  // чанк 1: шапка + разделитель + строка v60 (повтор шапки)
  const c1 = rem.chunks[1].split("\n");
  assert.equal(c1.length, 3);
  assert.match(c1[0], /C0 \| C1/);
  assert.match(c1[1], /--- \| ---/);
  assert.match(c1[2], /v60/);

  // инвариант счётчика строк
  assert.equal(rem.chunks.reduce((s, c) => s + c.split("\n").length - 2, 0), rem.remainingRows);
});

test("pipe-строки внутри fenced-кода не режутся", () => {
  const md =
    "Текст\n\n```\n| a | b |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n| 5 | 6 |\n```\n\nПосле";
  const parts = splitLargeTables(md, 3);
  assert.equal(parts.length, 1);
  assert.equal(parts[0].type, "md");
  assert.equal(parts[0].text, md);
});

test("fence с тильдами тоже распознаётся, а внутри не режется", () => {
  const md = "До\n\n~~~\n| a | b |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n| 5 | 6 |\n~~~\n\nПосле";
  const parts = splitLargeTables(md, 3);
  assert.equal(parts.length, 1);
  assert.equal(parts[0].type, "md");
});

test("отступ >=4 пробелов — индент-код, не таблица", () => {
  const md = "    | a | b |\n    | --- | --- |\n    | 1 | 2 |";
  const parts = splitLargeTables(md, 1);
  assert.equal(parts.length, 1);
  assert.equal(parts[0].type, "md");
  assert.equal(parts[0].text, md);
});

test("pipe-строки без строки-разделителя — не таблица", () => {
  const md = "| a | b |\n| c | d |\n| e | f |";
  const parts = splitLargeTables(md, 1);
  assert.equal(parts.length, 1);
  assert.equal(parts[0].type, "md");
  assert.equal(parts[0].text, md);
});

test("CRLF: те же точки разреза, \\r сохраняется в контенте", () => {
  const md = "До\r\n\r\n" + table(3, 5).replace(/\n/g, "\r\n") + "\r\n\r\nПосле";
  const parts = splitLargeTables(md, 3);
  assert.deepEqual(
    parts.map((p) => p.type),
    ["md", "md", "tableRemainder", "md"]
  );
  assert.equal(parts[2].remainingRows, 2);
  assert.equal(parts[2].totalRows, 5);
  // хвостовой \r не потерян (байтовое сохранение контента)
  assert.ok(parts[1].text.includes("\r"));
  assert.ok(parts[2].chunks[0].includes("\r"));
  // число строк в хвосте не зависит от переносов
  assert.equal(parts[2].chunks.reduce((s, c) => s + c.split("\n").length - 2, 0), 2);
});

test("без завершающего перевода строки последняя строка таблицы учитывается", () => {
  const md = table(3, 5); // без хвостового \n
  const parts = splitLargeTables(md, 3);
  assert.deepEqual(
    parts.map((p) => p.type),
    ["md", "tableRemainder"]
  );
  assert.equal(parts[1].totalRows, 5);
  assert.equal(parts[1].remainingRows, 2);
  assert.match(parts[1].chunks[0], /v40 \| v41 \| v42/);
});

test("дефолтный кап — константа модуля", () => {
  assert.equal(LARGE_TABLE_MAX_ROWS, 300);
  // строк данных ровно cap — не режется
  const fits = splitLargeTables(table(2, LARGE_TABLE_MAX_ROWS));
  assert.equal(fits.length, 1);
  // на строку больше — режется
  const over = splitLargeTables(table(2, LARGE_TABLE_MAX_ROWS + 1));
  assert.equal(over.length, 2);
  assert.equal(over[1].remainingRows, 1);
});

test("некорректный maxRows падает на дефолтный кап", () => {
  const parts = splitLargeTables(table(2, LARGE_TABLE_MAX_ROWS + 1), -5);
  assert.equal(parts.length, 2);
  assert.equal(parts[1].remainingRows, 1);
});
