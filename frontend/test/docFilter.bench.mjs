import { test } from "node:test";
import assert from "node:assert/strict";
import { globToRegExp, filterDocuments } from "../src/lib/docFilter.mjs";

// Бенчмарк чистой алгоритмики фильтра списка документов.
//
// Замеряет ровно тот путь, что исполняет DocumentList.jsx на каждое нажатие
// клавиши: свежая компиляция regex (globToRegExp) + прогон filterDocuments.
// Каждая итерация замера НЕ мемоизирует regex вне цикла — компиляция входит
// в стоимость, как в реальном компоненте (useMemo пересчитывается на каждый
// ввод символа).
//
// N=100/1000 — текущая реальность; N=10000/100000 — STRESS-тест на будущий
// рост (зафиксировать риск линейного роста), не оценка нынешней нагрузки.

const BUCKETS = 100;

const SCENARIOS = [
  { label: "100%", mask: "bucket*", expected: (n) => n },
  { label: "10%", mask: "bucket_00*", expected: (n) => countWhere(n, (b) => b < 10) },
  { label: "1%", mask: "bucket_003_*", expected: (n) => countWhere(n, (b) => b === 3) },
  { label: "0%", mask: "zzz_nomatch_*", expected: () => 0 },
];

function countWhere(n, pred) {
  let c = 0;
  for (let i = 0; i < n; i++) if (pred(i % BUCKETS)) c++;
  return c;
}

function makeDocs(n) {
  const docs = new Array(n);
  for (let i = 0; i < n; i++) {
    const bucket = i % BUCKETS;
    docs[i] = {
      id: String(i).padStart(16, "0"),
      filename: `bucket_${String(bucket).padStart(3, "0")}_${String(i).padStart(5, "0")}.docx`,
      uploaded_by: "demo.editor",
    };
  }
  return docs;
}

function percentile(sorted, q) {
  const k = (sorted.length - 1) * q;
  const lo = Math.floor(k);
  const hi = Math.min(lo + 1, sorted.length - 1);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (k - lo);
}

function measure(fn, reps) {
  const times = [];
  for (let r = 0; r < reps; r++) {
    const t0 = process.hrtime.bigint();
    fn();
    const t1 = process.hrtime.bigint();
    times.push(Number(t1 - t0) / 1e6);
  }
  times.sort((a, b) => a - b);
  return {
    p50: times[Math.floor((times.length - 1) * 0.5)],
    p95: percentile(times, 0.95),
  };
}

test("алгоритмика фильтра: globToRegExp + filterDocuments (задержка за одно нажатие)", () => {
  const NS = [100, 1000, 10000, 100000];
  const REPS = 20;

  const rows = [];
  for (const n of NS) {
    const docs = makeDocs(n);
    for (const s of SCENARIOS) {
      const { p50, p95 } = measure(() => {
        globToRegExp(s.mask); // свежая компиляция на каждую итерацию
        filterDocuments(docs, { mask: s.mask });
      }, REPS);

      const filtered = filterDocuments(docs, { mask: s.mask });
      const expected = s.expected(n);
      assert.equal(filtered.length, expected, `${n}/${s.label}: ожидалось ${expected}, получено ${filtered.length}`);

      rows.push({ n, match: s.label, count: filtered.length, p50, p95 });
    }
  }

  console.log("\n--- Фильтр: globToRegExp + filterDocuments (мс за одно нажатие) ---");
  console.log("N          маска  совпало   p50       p95");
  for (const r of rows) {
    console.log(
      `${String(r.n).padStart(9)}  ${r.match.padEnd(5)}  ${String(r.count).padStart(6)}  ` +
        `${r.p50.toFixed(3).padStart(7)}  ${r.p95.toFixed(3).padStart(7)}`
    );
  }
  console.log("(N=100/1000 — текущая реальность; N=10000/100000 — stress-тест на будущее)\n");
});
