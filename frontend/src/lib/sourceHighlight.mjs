import { decodeString } from 'micromark-util-decode-string';

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

// HAST text nodes retain offsets into the Markdown input. Map those offsets
// through split-table line maps before wrapping visible text; never highlight
// an entire paragraph merely because one source line intersects it.
export function rehypeSourceHighlights({text = '', canonicalText = text, spans = [], lineMap = null} = {}) {
  if (!spans.length) return () => {};
  const canonicalLines = [0];
  let count = 0;
  for (const char of canonicalText) {
    count += 1;
    if (char === '\n') canonicalLines.push(count);
  }
  const positions = [];
  let offset = 0, line = 0, column = 0;
  for (const char of text) {
    const sourceLine = lineMap ? lineMap[line] - 1 : line;
    const mapped = canonicalLines[sourceLine];
    positions[offset] = mapped == null ? null : mapped + column;
    if (char.length === 2) positions[offset + 1] = positions[offset];
    offset += char.length;
    if (char === '\n') {line += 1; column = 0;} else column += 1;
  }
  const wrap = (node, parent, context) => {
    const position = node.position || (parent.tagName === 'code' ? parent.position : null);
    if (!position || !node.value) return [node];
    const from = position.start.offset, to = position.end.offset;
    if (!Number.isInteger(from) || !Number.isInteger(to)) return [node];
    let raw = text.slice(from, to);
    let rawStart = 0, mapping;
    const code = parent.tagName === 'code';
    let indentation = 0, inlineCode = false;
    if (code) {
      const fence = /^( {0,3})(`{3,}|~{3,})[^\r\n]*\r?\n/.exec(raw);
      if (fence) {
        const close = new RegExp(`(?:^|\\n) {0,3}${fence[2][0]}{${fence[2].length},}[ \\t]*(?:\\r?\\n)?$`).exec(raw);
        const end = close ? close.index + (raw[close.index] === '\n' ? 1 : 0) : raw.length;
        rawStart = fence[0].length;
        raw = raw.slice(rawStart, end);
        indentation = fence[1].length;
      } else if (/^(?: {4}|\t)/.test(raw)) indentation = 4;
      else {
        const delimiter = /^`+/.exec(raw)?.[0];
        if (delimiter && raw.endsWith(delimiter)) {
          rawStart = delimiter.length;
          raw = raw.slice(rawStart, -delimiter.length);
          if (raw.startsWith(' ') && raw.endsWith(' ') && raw.trim()) {raw = raw.slice(1, -1); rawStart += 1;}
        }
        inlineCode = true;
      }
    }
    if (raw !== node.value && raw.includes(node.value)) {
      // For inline code the parser may include its delimiters in the position.
      const localStart = raw.indexOf(node.value);
      if (raw.indexOf(node.value, localStart + 1) >= 0) return [node];
      rawStart += localStart;
    } else if (raw !== node.value) {
      mapping = renderedSourceMap(raw, node.value, {...context, code, indentation, inlineCode});
      if (!mapping) return [node];
    }
    const pieces = [];
    let cursor = 0, value = '', previous = null;
    const flush = () => {
      if (!value) return;
      const child = {type: 'text', value};
      pieces.push(previous ? {type: 'element', tagName: 'mark', properties: {
        className: ['source-highlight-mark'], 'data-source-span-indices': previous,
      }, children: [child]} : child);
      value = '';
    };
    for (const char of node.value) {
      const local = mapping ? mapping[cursor] : cursor;
      const point = local == null ? null : positions[from + rawStart + local];
      const indices = point == null ? '' : spans.flatMap((span, index) =>
        point >= span.start && point < span.end ? [index] : []).join(' ');
      if (previous !== indices) {flush(); previous = indices;}
      value += char;
      cursor += char.length;
    }
    flush();
    return pieces;
  };
  return (tree) => {
    if (!spans.length) return;
    const visit = (parent, inherited = {}) => {
      if (!Array.isArray(parent.children)) return;
      const context = {quote: inherited.quote || parent.tagName === 'blockquote',
        list: inherited.list || parent.tagName === 'li'};
      parent.children = parent.children.flatMap(child => {
        if (child.type === 'text') return wrap(child, parent, context);
        visit(child, context);
        return [child];
      });
    };
    visit(tree);
  };
}

// Reuse the Markdown parser's decoder so entity/escape handling stays linear
// and agrees with rendered text, including two-code-point HTML entities.
function renderedSourceMap(raw, rendered, {code = false, indentation = 0, inlineCode = false, quote = false, list = false} = {}) {
  const mapping = [];
  let r = 0, v = 0;
  while (r < raw.length && v < rendered.length) {
    if (r === 0 || raw[r - 1] === '\n') {
      if (quote) {
        const marker = /^(?: {0,3}>[ \t]?)+/.exec(raw.slice(r));
        if (marker) r += marker[0].length;
      }
      if (code && indentation) {
        let columns = 0;
        while (columns < indentation && (raw[r] === ' ' || raw[r] === '\t')) {
          columns += raw[r] === '\t' ? 4 - columns % 4 : 1; r += 1;
        }
      } else if (!code && list && raw[r] !== rendered[v]) {
        r += /^[ \t]*/.exec(raw.slice(r))[0].length;
      }
    }
    const trailing = rendered[v] === '\n' && /^[ \t]+(?=\r?\n)/.exec(raw.slice(r));
    if (trailing) {r += trailing[0].length; continue;}
    const token = !code && ((raw[r] === '&' && /^&(?:#[xX][\da-fA-F]{1,6}|#\d{1,7}|[a-zA-Z][a-zA-Z\d]{0,30});/.exec(raw.slice(r)))
      || (raw[r] === '\\' && /^\\[!-/:-@[-`{-~]/.exec(raw.slice(r))));
    if (token) {
      const decoded = decodeString(token[0]);
      if (decoded !== token[0]) {
        if (!rendered.startsWith(decoded, v)) return null;
        mapping.push(...Array(decoded.length).fill(r));
        r += token[0].length; v += decoded.length; continue;
      }
    }
    if (inlineCode && (raw[r] === '\n' || raw[r] === '\r') && rendered[v] === ' ') {
      mapping.push(r); r += raw[r] === '\r' && raw[r + 1] === '\n' ? 2 : 1; v += 1;
    } else if (raw[r] === '\r' && rendered[v] === '\n') {
      mapping.push(r); r += raw[r + 1] === '\n' ? 2 : 1; v += 1;
    } else if (raw[r] === rendered[v]) {
      mapping.push(r); r += 1; v += 1;
    } else return null;
  }
  // Code blocks always render a final newline, including a source without one.
  if (code && r === raw.length && v === rendered.length - 1 && rendered[v] === '\n') {mapping.push(null); v += 1;}
  return (r === raw.length || /^[ \t]+$/.test(raw.slice(r))) && v === rendered.length ? mapping : null;
}
