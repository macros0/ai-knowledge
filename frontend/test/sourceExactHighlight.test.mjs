import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import * as highlighting from '../src/lib/sourceHighlight.mjs';

function render(text, canonical, spans, lineMap) {
  return renderToStaticMarkup(React.createElement(ReactMarkdown, {
    remarkPlugins: [remarkGfm], rehypePlugins: [[highlighting.rehypeSourceHighlights,
      {text, canonicalText: canonical, spans, lineMap}]],
  }, text));
}

test('only the exact IT0004 line is highlighted inside a long Markdown paragraph', () => {
  const text = '693661 Performance\n521396 IT0004(Challenge) and relevance to Sec80-U\n2507472 Payroll';
  const start = text.indexOf('521396');
  const end = text.indexOf('\n2507472');
  const html = render(text, text, [{start, end}]);
  assert.match(html, /693661 Performance\n<mark[^>]+>521396 IT0004\(Challenge\) and relevance to Sec80-U<\/mark>\n2507472/);
});

test('canonical Python offsets survive emoji, bold markup and table line maps', () => {
  const canonical = '😀 Intro\n\n| Field | Value |\n|---|---|\n| A | First |\n| B | **Target** |';
  const text = '| Field | Value |\n|---|---|\n| B | **Target** |';
  const start = Array.from(canonical.slice(0, canonical.indexOf('Target'))).length;
  const html = render(text, canonical, [{start, end: start + 6}], [3, 4, 6]);
  assert.match(html, /<strong><mark[^>]+>Target<\/mark><\/strong>/);
  assert.doesNotMatch(html, /<mark[^>]+>Field/);
});

test('multiple separate ranges retain their own navigation indices', () => {
  const html = render('First and second', 'First and second', [{start:0,end:5},{start:10,end:16}]);
  assert.match(html, /data-source-span-indices="0">First/);
  assert.match(html, /data-source-span-indices="1">second/);
});

test('Windows line endings preserve the exact highlight after a line break', () => {
  const text = 'Earlier line\r\nTarget passage\r\nLater line';
  const html = render(text, text, [{start:14, end:28}]);
  assert.match(html, /<mark[^>]+>Target passage<\/mark>/);
  assert.doesNotMatch(html, /<mark[^>]+>Earlier/);
});

for (const prefix of ['Earlier &amp; ', 'Earlier \\* ', 'Earlier &#128512; ']) {
  test(`escaped Markdown prefix retains offsets: ${prefix}`, () => {
    const text = `${prefix}Target passage later`;
    const start = Array.from(prefix).length;
    const html = render(text, text, [{start, end:start + 14}]);
    assert.match(html, /<mark[^>]+>Target passage<\/mark>/);
  });
}

test('fenced code inherits its source position without highlighting the fence', () => {
  const text = '```text\nTarget passage\n```';
  const html = render(text, text, [{start:8, end:22}]);
  assert.match(html, /<mark[^>]+>Target passage<\/mark>/);
});

test('PDF line wrapping with trailing spaces retains later exact ranges', () => {
  const text = 'Support for India Payroll \n521396 IT0004(Challenge) and relevance to Sec80-U';
  const start = text.indexOf('521396');
  const html = render(text, text, [{start, end:text.length}]);
  assert.match(html, /<mark[^>]+>521396 IT0004\(Challenge\) and relevance to Sec80-U<\/mark>/);
});

test('a long paragraph with many entities highlights without recursive alignment', () => {
  const prefix = 'A &amp; '.repeat(2500);
  const text = prefix + 'Target passage';
  const html = render(text, text, [{start:prefix.length, end:text.length}]);
  assert.match(html, /<mark[^>]+>Target passage<\/mark>/);
});

for (const text of ['```text\r\nTarget passage\r\n```', '> Earlier\n> Target passage',
  '- Earlier\n  Target passage', '    Earlier\n    Target passage']) {
  test(`container formatting preserves the selected text: ${JSON.stringify(text.slice(0, 12))}`, () => {
    const start = text.indexOf('Target');
    const html = render(text, text, [{start,end:start+14}]);
    assert.match(html, /<mark[^>]+>Target passage<\/mark>/);
  });
}
