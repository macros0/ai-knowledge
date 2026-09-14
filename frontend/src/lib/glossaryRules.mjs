const whitespace = /\s/;

function parserError(message) {
  return new Error(`Префиксы должны быть списком строк в кавычках: ${message}`);
}

/** Parse the admin rule field while preserving spaces inside each quote. */
export function parseGlossaryRulePrefixes(input) {
  if (typeof input !== "string") throw parserError("ожидалась строка");
  const result = [];
  const normalized = new Set();
  let index = 0;
  while (index < input.length) {
    while (index < input.length && whitespace.test(input[index])) index += 1;
    if (index >= input.length) break;
    if (input[index] !== '"') throw parserError("каждый префикс должен начинаться с \"");
    index += 1;
    let value = "";
    let closed = false;
    while (index < input.length) {
      const char = input[index++];
      if (char === "\\") {
        if (index >= input.length || !['"', "\\"].includes(input[index])) {
          throw parserError("разрешено экранировать только кавычку и обратный слеш");
        }
        value += input[index++];
      } else if (char === '"') {
        closed = true;
        break;
      } else {
        value += char;
      }
    }
    if (!closed) throw parserError("не закрыта кавычка");
    if (!value) throw parserError("пустой префикс запрещён");
    const normalizedValue = value.normalize("NFC").toLowerCase().replace(/\s+/g, " ");
    if (normalized.has(normalizedValue)) throw parserError("префиксы не должны дублироваться");
    normalized.add(normalizedValue);
    result.push(value);
    while (index < input.length && whitespace.test(input[index])) index += 1;
    if (index >= input.length) break;
    if (input[index] !== ",") throw parserError("между префиксами нужна запятая");
    index += 1;
    let next = index;
    while (next < input.length && whitespace.test(input[next])) next += 1;
    if (next >= input.length) throw parserError("после запятой нужен префикс");
    index = next;
  }
  if (!result.length) throw parserError("нужен хотя бы один префикс");
  return result;
}

export function formatGlossaryRulePrefixes(prefixes) {
  return prefixes.map((prefix) => JSON.stringify(prefix)).join(", ");
}
