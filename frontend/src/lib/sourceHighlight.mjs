export function sourceSpansToLineRanges(text, sourceSpans = []) {
  return sourceSpans.map(({ start, end }) => {
    const codepoints = Array.from(text);
    const before = codepoints.slice(0, start).join("");
    const selected = codepoints.slice(start, end).join("");
    const first = before.split("\n").length;
    const newlineCount = (selected.match(/\n/g) || []).length;
    const last = first + newlineCount - (selected.endsWith("\n") ? 1 : 0);
    return [first, last];
  });
}

export function splitRawSourceSpans(text, sourceSpans = []) {
  const points = Array.from(text);
  const ranges = sourceSpans
    .map(({ start, end }, spanIndex) => ({ start, end, spanIndex }))
    .filter(({ start, end }) => Number.isInteger(start) && Number.isInteger(end) && 0 <= start && start < end && end <= points.length)
    .sort((a, b) => a.start - b.start);
  const segments = [];
  let cursor = 0;
  for (const { start, end, spanIndex } of ranges) {
    if (end <= cursor) continue;
    if (start > cursor) segments.push({ text: points.slice(cursor, start).join(""), spanIndex: null });
    segments.push({ text: points.slice(Math.max(start, cursor), end).join(""), spanIndex });
    cursor = end;
  }
  if (cursor < points.length) segments.push({ text: points.slice(cursor).join(""), spanIndex: null });
  return segments;
}
