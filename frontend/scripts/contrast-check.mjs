#!/usr/bin/env node
// Проверка контраста тем интерфейса (WCAG AA).
//
// Читает CSS-токены палитры прямо из src/app/globals.css (тёмная = :root,
// светлая = :root[data-theme="light"]) и проверяет целевые пары "текст/фон".
// Критерий приёмки: светлая тема — строгий AA (>= 4.5:1); тёмная — информационно
// (нижняя граница 3:1 для заливок/UI, т.к. она не редизайнится в этой задаче).
// Код выхода 1 при провале светлой темы.
//
//   node frontend/scripts/contrast-check.mjs

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const cssPath = join(dirname(fileURLToPath(import.meta.url)), "..", "src", "app", "globals.css");
const css = readFileSync(cssPath, "utf8");

const NORMAL_MIN = 4.5;

function extractVars(block) {
  const vars = {};
  for (const m of block.matchAll(/--([\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;/g)) {
    vars[m[1]] = m[2];
  }
  return vars;
}

function grabRootBlock(cssText, selector) {
  const esc = selector.replace(/[.*[\]"=]/g, "\\$&");
  const re = new RegExp(esc + "\\s*\\{([^}]*)\\}", "m");
  const m = re.exec(cssText);
  return m ? m[1] : "";
}

function hexToRgb(hex) {
  let h = hex.replace("#", "");
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
}

function channel(v) {
  const c = v / 255;
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function luminance(hex) {
  const [r, g, b] = hexToRgb(hex).map(channel);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// Спец-переопределения вне CSS-токенов (см. globals.css): текст .doc-problem-badge.
const EXTRA = {
  dark: { "color-amber-deep": "#b8860b" },
  light: { "color-amber-deep": "#7a5000" },
};

// Роли "текст/фон". Имя может быть токеном или "#..."-литералом.
const PAIRS = [
  ["text", "bg"],
  ["text", "panel"],
  ["muted", "bg"],
  ["muted", "panel"],
  ["muted", "accent-dim"],
  ["accent", "bg"],
  ["accent", "panel"],
  ["accent", "accent-dim"],
  ["color-amber", "panel"],
  ["color-amber", "bg"],
  ["color-amber-deep", "panel"],
  ["color-green", "panel"],
  ["color-green", "bg"],
  ["color-orange", "panel"],
  ["color-orange", "bg"],
  ["color-type-ref", "panel"],
  ["color-type-example", "panel"],
  // Белый текст на заливках (активные кнопки/табы/чипы, тосты).
  ["#ffffff", "accent"],
  ["#ffffff", "danger"],
  ["#ffffff", "ok"],
];

function resolvePalette(theme) {
  const vars = extractVars(
    grabRootBlock(css, theme === "light" ? ':root[data-theme="light"]' : ":root")
  );
  return { ...vars, ...EXTRA[theme] };
}

let failed = 0;
for (const theme of ["dark", "light"]) {
  const pal = resolvePalette(theme);
  const themeName = theme === "dark" ? "Тёмная (:root)" : "Светлая (data-theme=light)";
  console.log("\n=== " + themeName + " ===");
  // Тёмная тема не редизайнится в рамках этой задачи — существующие пары
  // проверяются информационно (нижняя граница 3:1 для UI/заливок, hard-fail
  // только ниже 3). Светлая — новая, критерий приёмки строгий: AA >= 4.5:1.
  const hard = theme === "light";
  const min = hard ? 4.5 : 3.0;
  const missing = new Set();
  for (const [fgName, bgName] of PAIRS) {
    const fg = fgName.startsWith("#") ? fgName : pal[fgName];
    const bg = bgName.startsWith("#") ? bgName : pal[bgName];
    if (!fg) missing.add(fgName);
    if (!bg) missing.add(bgName);
    if (!fg || !bg) continue;
    const r = ratio(fg, bg);
    const ok = r >= min;
    const mark = ok ? "ok  " : hard ? "FAIL" : "low ";
    if (!ok && hard) failed++;
    console.log(
      mark + " " + fgName.padEnd(18) + " on " + bgName.padEnd(14) + " -> " + r.toFixed(2) + ":1 (min " + min + ":1)"
    );
  }
  if (missing.size) {
    console.error("Не найдены токены: " + [...missing].join(", "));
    failed++;
  }
}

console.log(failed ? "\nПРОВАЛОВ (светлая тема < 4.5:1): " + failed : "\nСветлая тема проходит WCAG AA (>= 4.5:1)");
process.exit(failed ? 1 : 0);
