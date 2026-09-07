// Разбиение markdown на части для просмотра документов.
//
// Гигантские GFM-таблицы (порядка тысяч строк) заставляют remark-gfm парсить
// суперлинейно (~20 с на ~300 КБ одной таблицы) — это и есть причина зависания
// страницы «Весь документ» и одиночных чанков с такими таблицами. Дорого стоит
// именно парсинг (mdast), а не отрисовка DOM, поэтому кап по уже построенному
// AST не помогает — режем ТЕКСТ до парсинга.
//
// Таблицу длиннее `maxRows` строк режем: markdown-часть сохраняет шапку +
// разделитель + первые `maxRows` строк (валидная GFM-таблица), а хвост
// выносится в отдельную часть `tableRemainder`. Хвост нарезается на чанки по
// `maxRows` строк — каждый чанк с повторённой шапкой/разделителем образует
// свою маленькую GFM-таблицу. Парсинг многих маленьких таблиц линеен и дёшев
// (~1 с на 4300 строк) в отличие от суперлинейного парсинга одной гигантской
// (~20 с), поэтому рендер остаётся настоящей таблицей без зависания. Остальной
// текст остаётся без вырезок.
//
// Функция чистая и детерминированная (одинаковый вход → одинаковый выход на
// сервере и клиенте) — никаких платформозависимых API. Разбиение по "\n",
// хвостовой "\r" (CRLF) сохраняется в строках: детектирующие regex толерантны
// к нему через класс `\s`, а пересборка частей через "\n" байтово сохраняет
// контент (remark всё равно нормализует переносы, raw-режим не трогается).

export const LARGE_TABLE_MAX_ROWS = 300;

const PIPE_RE = /^\s{0,3}\|/;
const SEP_RE = /^\s{0,3}\|[\s:|-]+\|?\s*$/;
const FENCE_RE = /^\s{0,3}(`{3,}|~{3,})/;

// Нарезает строки хвоста таблицы на валидные GFM-таблицы по `chunkSize` строк,
// повторяя шапку и разделитель перед каждым чанком.
function chunkRows(header, sep, rows, chunkSize) {
  const chunks = [];
  for (let i = 0; i < rows.length; i += chunkSize) {
    chunks.push([header, sep, ...rows.slice(i, i + chunkSize)].join("\n"));
  }
  return chunks;
}

export function splitLargeTables(markdown, maxRows = LARGE_TABLE_MAX_ROWS) {
  const text = markdown == null ? "" : String(markdown);
  const cap = Number.isFinite(maxRows) && maxRows >= 0 ? maxRows : LARGE_TABLE_MAX_ROWS;
  const lines = text.split("\n");

  const parts = [];
  let buffer = [];
  let fence = null; // null | { char: "`" | "~", len: number }

  const flush = () => {
    if (buffer.length) {
      parts.push({ type: "md", text: buffer.join("\n") });
      buffer = [];
    }
  };

  const fenceOf = (line) => {
    const m = line.match(FENCE_RE);
    return m ? { char: m[1][0], len: m[1].length } : null;
  };

  const applyTable = (block) => {
    // GFM-таблица обязана иметь шапку + строку-разделитель (block[1]). Одиночная
    // pipe-строка или набор строк без разделителя — не таблица, оставляем как
    // обычный текст (remark-парсер дешёв на таких данных).
    if (block.length < 2 || !SEP_RE.test(block[1])) {
      buffer.push(...block);
      return;
    }
    const dataRows = block.length - 2;
    if (dataRows <= cap) {
      buffer.push(...block);
      return;
    }
    flush();
    // `tablePreview: true` помечает превью-таблицу: рендерится с ограниченной
    // высотой (внутренний скролл), чтобы кнопка «показать остальные» оставалась
    // рядом, а не уходила за тысячи строк превью.
    parts.push({ type: "md", text: block.slice(0, 2 + cap).join("\n"), tablePreview: true });
    const header = block[0];
    const sep = block[1];
    const remainder = block.slice(2 + cap);
    parts.push({
      type: "tableRemainder",
      chunks: chunkRows(header, sep, remainder, cap),
      shownRows: cap,
      // remainingRows выводится из фактического хвоста — число в кнопке всегда
      // совпадает с количеством строк в chunks (минус повторённые шапки).
      remainingRows: remainder.length,
      totalRows: cap + remainder.length,
    });
  };

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    if (fence) {
      // Внутри fenced-кода: pipe-строки — литеральный текст, не таблица.
      const closing = fenceOf(line);
      if (closing && closing.char === fence.char && closing.len >= fence.len) fence = null;
      buffer.push(line);
      i += 1;
      continue;
    }

    const opening = fenceOf(line);
    if (opening) {
      fence = opening;
      buffer.push(line);
      i += 1;
      continue;
    }

    if (PIPE_RE.test(line)) {
      const start = i;
      while (i < lines.length && PIPE_RE.test(lines[i])) i += 1;
      applyTable(lines.slice(start, i));
      continue;
    }

    buffer.push(line);
    i += 1;
  }

  flush();
  return parts;
}
