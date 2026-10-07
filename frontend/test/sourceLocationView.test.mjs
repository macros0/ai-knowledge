import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {runInNewContext} from 'node:vm';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {transformSync} from 'next/dist/build/swc/index.js';

const require = createRequire(import.meta.url);
const compiled = transformSync(readFileSync(new URL('../src/components/SourceLocationView.jsx', import.meta.url), 'utf8'), {
  filename: 'SourceLocationView.jsx',
  jsc: {parser: {syntax: 'ecmascript', jsx: true}, target: 'es2020', transform: {react: {runtime: 'automatic'}}},
  module: {type: 'commonjs'},
}).code;

function mount(props) {
  const slots = [], viewers = [];
  let cursor = 0;
  const hooks = {...React,
    useRef(value) {const index = cursor++; return slots[index] ??= {current: value};},
    useState(value) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof value === 'function' ? value() : value;
      return [slots[index], value => {slots[index] = typeof value === 'function' ? value(slots[index]) : value;}];
    },
    useEffect() {},
  };
  const compiledModule = {exports: {}};
  runInNewContext(compiled, {module: compiledModule, exports: compiledModule.exports, require(id) {
    if (id === 'react') return hooks;
    if (id === '@/i18n/LocaleContext') return {useI18n: () => ({t: (key, args) => `${key}:${args?.index ?? ''}`})};
    if (id === '@/components/ContentViewer') return function ContentViewerProbe(props) {viewers.push(props); return React.createElement('p', null, props.text);};
    if (id === './MailMetadata') return () => null;
    return require(id);
  }});
  const find = (node, predicate) => {
    if (!node || typeof node !== 'object') return null;
    if (predicate(node)) return node;
    for (const child of React.Children.toArray(node.props?.children)) {
      const found = find(child, predicate);
      if (found) return found;
    }
    return null;
  };
  let tree;
  return {
    render() {cursor = 0; viewers.length = 0; tree = compiledModule.exports.default(props); return renderToStaticMarkup(tree);},
    get viewers() {return viewers;},
    toggle(index, open) {
      const chunk = find(tree, node => node.props?.id === `source-chunk-${index}`);
      const details = find(chunk, node => node.type === 'details');
      assert.ok(details, 'other chunks must be available for explicit expansion');
      details.props.onToggle({currentTarget: {open}});
    },
  };
}
const chunks = Array.from({length: 111}, (_, chunk_index) => ({chunk_index, content: `chunk ${chunk_index}`}));
const location = {status: 'exact', chunk_index: 24, spans: [{start: 0, end: 5, quote: 'chunk'}]};

test('jump to evidence renders the target immediately without parsing the entire document', () => {
  const view = mount({docId: 'doc', chunks, location, showAll: true});
  const html = view.render();
  assert.equal(view.viewers.length, 1);
  assert.equal(view.viewers[0].text, 'chunk 24');
  assert.equal(view.viewers[0].sourceSpans, location.spans);
  assert.equal((html.match(/id="source-chunk-/g) || []).length, 111);
});
test('other chunks remain readable through explicit expansion and collapse', () => {
  const view = mount({docId: 'doc', chunks, location, showAll: true});
  view.render();
  view.toggle(106, true); view.render();
  assert.deepEqual(view.viewers.map(v => v.text), ['chunk 24', 'chunk 106']);
  view.toggle(106, false); view.render();
  assert.deepEqual(view.viewers.map(v => v.text), ['chunk 24']);
});
test('single chunk and multiple evidence ranges still reach the content viewer', () => {
  const spans = [...location.spans, {start: 6, end: 8, quote: '24'}];
  const view = mount({docId: 'doc', chunks, location: {...location, spans}});
  const html = view.render();
  assert.equal(view.viewers.length, 1);
  assert.equal(view.viewers[0].sourceSpans, spans);
  assert.match(html, /sourceLocation.navigation/);
});
