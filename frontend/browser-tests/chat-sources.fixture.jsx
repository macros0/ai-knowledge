"use client";

// Installed only in an isolated acceptance build by prepare-chat-ui-acceptance.
import { Profiler, useCallback, useEffect, useRef, useState } from "react";
import ChatMessageView from "@/components/ChatMessageView";
import ChatComposer from "@/components/ChatComposer";
import { applyAnswerEvent, toggleChatSources } from "@/lib/chatAnswerState.mjs";
import { updateSelection, selectableSources } from "@/lib/chatSourceSelection.mjs";
import { createChatDeltaBuffer } from "@/lib/chatRenderState.mjs";

const noop = () => {};
const paint = () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
const log = (data) => console.info("chat-ui-acceptance", JSON.stringify(data));
function makeSources(count) {
  return Array.from({ length: count }, (_, i) => ({ source_index: i + 1,
    doc_id: `fixture-document-${Math.floor(i / 10)}`, filename: `Document ${Math.floor(i / 10)}.docx`,
    title: `Source ${i + 1}`, source_slug: `source-${i + 1}`, point_type: "concept", score: 0.9,
    snippet: "Synthetic source for rendering acceptance. ".repeat(4),
    selectable: true, in_model_context: true,
  }));
}
export default function Fixture() {
  const [count, setCount] = useState(200), [history, setHistory] = useState(10);
  const [baseline, setBaseline] = useState(false), [messages, setMessages] = useState([]);
  const [report, setReport] = useState("Ready"), [running, setRunning] = useState(false);
  const [paintReport, setPaintReport] = useState(null);
  const root = useRef(null), buffer = useRef(null);
  const sample = useRef(0);
  useEffect(() => {
    const observer = new PerformanceObserver((list) => list.getEntries().forEach((e) => log({ event: "longtask", duration: e.duration })));
    observer.observe({ type: "longtask", buffered: false });
    return () => { observer.disconnect(); buffer.current?.discard(); };
  }, []);
  const select = useCallback((index, indexes, checked) => setMessages((items) => items.map((m, i) => i === index
    ? { ...m, selectedSourceIndexes: updateSelection(m.selectedSourceIndexes ?? [], indexes, checked, selectableSources(m)) } : m)), []);
  const toggle = useCallback((index, open) => setMessages((items) => toggleChatSources(items, index, open)), []);
  async function load(sourceCount = count, messageCount = history) {
    buffer.current?.discard();
    const start = performance.now(), sources = makeSources(sourceCount);
    setMessages(Array.from({ length: messageCount }, (_, i) => ({ role: "assistant", attemptId: `fixture-${i}`,
      responseMode: "full", text: "Synthetic answer [1].", sources, sourcesOpen: true })));
    await paint();
    const result = { event: "sources_paint", sample: ++sample.current, count: sourceCount, history: messageCount,
      baseline, duration: performance.now() - start, rows: root.current.querySelectorAll("a.source-link").length };
    log(result); setPaintReport(result);
  }
  function burst() {
    const attemptId = messages.at(-1)?.attemptId;
    if (!attemptId) return;
    buffer.current?.discard();
    buffer.current = createChatDeltaBuffer({ onFlush: (text) => setMessages((items) => applyAnswerEvent(items, { type: "delta", attemptId, text })) });
    for (let i = 0; i < 100; i++) buffer.current.append(" Streaming ");
  }
  async function checks() {
    setRunning(true); setReport("Running");
    const passed = [];
    function check(name, ok) { if (!ok) throw new Error(name); passed.push(name); }
    try {
      await load(500, 1);
      let lists = root.current.querySelectorAll("details.sources");
      check("500 complete sources", lists[0].querySelectorAll("a.source-link").length === 500);
      lists[0].querySelectorAll(".source-select")[499].click(); await paint();
      lists[0].querySelector("summary").click(); await paint();
      check("collapse unmounts rows", lists[0].querySelectorAll("li").length === 0);
      lists[0].querySelector("summary").click(); await paint();
      check("last selection survives reopen", lists[0].querySelectorAll(".source-select")[499].checked);
      check("last link and citation retained", lists[0].querySelectorAll("a.source-link")[499].href.endsWith("/source-500.md")
        && root.current.querySelector("a.cite").href.endsWith("/source-1.md"));
      await load(200, 10);
      lists = root.current.querySelectorAll("details.sources");
      check("10 history messages retained", lists.length === 10);
      check("only latest mounts 200 rows", root.current.querySelectorAll("a.source-link").length === 200
        && lists[9].open && [...lists].slice(0, 9).every((list) => !list.open));
      lists[0].querySelector("summary").click(); await paint();
      check("old list opens all 200", lists[0].querySelectorAll("a.source-link").length === 200);
      setMessages((items) => [...items, { ...items[9], attemptId: "fixture-new", sourcesTouched: false }]); await paint();
      lists = root.current.querySelectorAll("details.sources");
      check("manual old opening survives new answer", lists[0].open && lists[0].querySelectorAll("a.source-link").length === 200);
      check("previous automatic list collapses", !lists[9].open && lists[9].querySelectorAll("li").length === 0);
      setReport(JSON.stringify({ status: "PASS", checks: passed }, null, 2));
    } catch (error) { setReport(JSON.stringify({ status: "FAIL", error: error.message, passed }, null, 2)); }
    finally { setRunning(false); }
  }
  return <section className="panel" ref={root} onInputCapture={() => {
    const start = performance.now(); paint().then(() => log({ event: "input_paint", duration: performance.now() - start }));
  }}>
    <h2>Chat source acceptance</h2>
    <label>Sources<select aria-label="Source count" value={count} onChange={(e) => setCount(Number(e.target.value))}>
      {[0, 167, 200, 500].map((n) => <option key={n}>{n}</option>)}</select></label>
    <label>Messages<select aria-label="History size" value={history} onChange={(e) => setHistory(Number(e.target.value))}>
      {[1, 10].map((n) => <option key={n}>{n}</option>)}</select></label>
    <label><input type="checkbox" aria-label="Baseline all lists" checked={baseline} disabled={running}
      onChange={(e) => setBaseline(e.target.checked)} />Baseline all lists</label>
    <button onClick={() => load()} disabled={running}>Load sources</button>
    <button onClick={() => { buffer.current?.discard(); setMessages([]); }}>Clear sources</button>
    <button onClick={burst}>100 deltas</button>
    <button onClick={checks} disabled={running || baseline}>Run checks</button>
    <pre role="status" data-testid="acceptance-report">{report}</pre>
    <output data-testid="paint-report" data-sample={paintReport?.sample}>{JSON.stringify(paintReport)}</output>
    <Profiler id="History" onRender={(id, phase, duration) => log({ event: "commit", id, phase, duration })}>
      <div className="chat-log"><div className="chat-log-content">{messages.map((m, i) => <ChatMessageView key={i}
        message={m} index={i} isLatest={baseline || i === messages.length - 1} isPending={false} actionsPending={false}
        searchDepthMax={500} onCopy={noop} onSelect={select} onAnswerSelected={noop} onAddToScope={noop}
        onRetry={noop} onRepeatWithoutGlossary={noop} onStop={noop} onToggleSources={toggle} />)}</div></div>
      <ChatComposer disabled={false} onSubmit={noop} />
    </Profiler>
  </section>;
}
