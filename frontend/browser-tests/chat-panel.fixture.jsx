"use client";

// Installed in an isolated acceptance build only. Auth/settings remain real.
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import Link from "next/link";
import ChatPanel from "@/components/ChatPanel";
import { useChat } from "@/context/ChatContext";
import { useI18n } from "@/i18n/LocaleContext";

const paint = () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
const REPORT_KEY = "chat-panel-acceptance-report";
const sources = (count, prefix) => Array.from({ length: count }, (_, i) => ({
  source_index: i + 1, doc_id: "acceptance-document", filename: "Acceptance.docx",
  source_slug: `${prefix}-${i + 1}`, title: `${prefix} source ${i + 1}`, point_type: "concept",
  score: 0.9, selectable: true, in_model_context: true, snippet: "Synthetic acceptance source.",
}));

function installTransport() {
  const original = window.fetch;
  const streams = [], cancellations = [];
  window.fetch = async (input, init) => {
    const pathname = new URL(typeof input === "string" ? input : input.url, window.location.href).pathname;
    if (pathname === "/api/chat/stream") {
      const entry = { request: JSON.parse(init.body), signal: init.signal, closed: false };
      const body = new ReadableStream({
        start(controller) { entry.controller = controller; },
        cancel() { entry.closed = true; },
      });
      // Deliberately allow late events after AbortSignal, exercising controller guards.
      entry.send = (event) => entry.controller.enqueue(new TextEncoder().encode(`${JSON.stringify(event)}\n`));
      streams.push(entry);
      return new Response(body, { headers: { "Content-Type": "application/x-ndjson" } });
    }
    if (/^\/api\/chat\/attempts\/[^/]+\/cancel$/.test(pathname)) {
      cancellations.push({ pathname, body: JSON.parse(init.body) });
      return Response.json({ cancelled: true });
    }
    return original.call(window, input, init);
  };
  return { streams, cancellations, restore() {
    window.fetch = original;
    for (const entry of streams) if (!entry.closed) { entry.controller.close(); entry.closed = true; }
  } };
}

export default function ChatPanelAcceptance() {
  const root = useRef(null), transport = useRef(null), current = useRef(null);
  const chat = useChat();
  useLayoutEffect(() => { current.current = chat; }, [chat]);
  const { t } = useI18n();
  const [running, setRunning] = useState(false), [report, setReport] = useState({ status: "READY" });
  const [previousResult, setPreviousResult] = useState(false);
  useEffect(() => {
    let active = true;
    const saved = sessionStorage.getItem(REPORT_KEY);
    if (saved) queueMicrotask(() => {
      if (active) { setReport(JSON.parse(saved)); setPreviousResult(true); }
    });
    return () => { active = false; transport.current?.restore(); };
  }, []);

  async function until(condition, name) {
    const deadline = performance.now() + 5000;
    while (!condition()) {
      if (performance.now() > deadline) throw new Error(`Timeout: ${name}`);
      await paint();
    }
    await paint();
  }
  const messages = () => root.current.querySelectorAll(".msg.assistant");
  const lists = () => root.current.querySelectorAll("details.sources");
  const rows = (index) => lists()[index].querySelectorAll("a.source-link");
  const button = (key) => [...root.current.querySelectorAll("button")].find((el) => el.textContent === t(key));
  async function submit(query, mode) {
    [...root.current.querySelectorAll('[role="radio"]')].find((el) => el.textContent === t(`chat.responseMode.${mode}.label`)).click();
    await paint();
    const input = root.current.querySelector(".chat-form input");
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, query);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    await paint();
    root.current.querySelector('.chat-form button[type="submit"]').click();
  }
  function save(value, completedRun = true) {
    const result = completedRun ? { ...value, completed_at: new Date().toISOString() } : value;
    setReport(result);
    sessionStorage.setItem(REPORT_KEY, JSON.stringify(result));
    console.info("chat-panel-acceptance", JSON.stringify(result));
  }
  async function checks() {
    setRunning(true); setPreviousResult(false); setReport({ status: "RUNNING" });
    const passed = [];
    const check = (name, ok) => { if (!ok) throw new Error(name); passed.push(name); };
    try {
      if (!chat.settings.response_modes?.includes("documents")) throw new Error("Wait for real chat settings with response modes");
      transport.current?.restore(); transport.current = installTransport();
      const io = transport.current;
      if (chat.messages.length) { button("chat.newChat").click(); await until(() => !current.current.messages.length, "new chat"); }
      await submit("Acceptance first", "fast");
      await until(() => io.streams.length === 1, "first request");
      const first = io.streams[0];
      first.send({ type: "sources", sources: sources(500, "first") });
      first.send({ type: "delta", text: "FIRST streamed text" });
      await until(() => lists().length === 1 && rows(0).length === 500, "500 sources");
      check("fast request through real API parser", first.request.response_mode === "fast");
      check("all 500 sources rendered", rows(0).length === 500);
      lists()[0].querySelectorAll(".source-select")[499].click(); await paint();
      check("last source selected", current.current.messages[1].selectedSourceIndexes.includes(500));
      button("chat.stopAnswer").click();
      await until(() => !current.current.pending, "stopped request");
      const stoppedText = messages()[0].querySelector(".bubble").textContent;
      check("stop aborts fetch", first.signal.aborted);
      check("stop updates answer", stoppedText === t("chat.answerStopped"));
      check("cancel uses stopped attempt and session", io.cancellations.length === 1
        && io.cancellations[0].pathname === `/api/chat/attempts/${first.request.attempt_id}/cancel`
        && io.cancellations[0].body.session_id === first.request.session_id);

      await submit("Acceptance second", "documents");
      await until(() => io.streams.length === 2, "second request");
      const second = io.streams[1];
      check("second request uses documents mode", second.request.response_mode === "documents");
      check("new request has a new attempt", second.request.attempt_id !== first.request.attempt_id);
      check("new request keeps session", second.request.session_id === first.request.session_id);
      check("old automatic sources collapse", !lists()[0].open && rows(0).length === 0);
      const activeText = messages()[1].querySelector(".bubble").textContent;
      first.send({ type: "delta", text: "LATE old delta" });
      first.send({ type: "sources", sources: sources(1, "LATE") });
      first.send({ type: "progress", phase: "retrieval", search_depth: 1, search_limit_reached: true });
      first.send({ type: "result", data: { answer: "LATE old result", sources: sources(1, "LATE") } });
      await until(() => first.closed, "late old result consumed");
      check("late events leave stopped text unchanged", messages()[0].querySelector(".bubble").textContent === stoppedText);
      check("late events leave new answer unchanged", messages()[1].querySelector(".bubble").textContent === activeText);
      check("late completion keeps new request pending", current.current.pending);
      check("stopped source data remains complete", current.current.messages[1].sources.length === 500);

      second.send({ type: "sources", sources: sources(200, "second") });
      second.send({ type: "delta", text: "Buffered second delta" });
      second.send({ type: "result", data: { answer: "FINAL second [200]", sources: sources(200, "final-second"), search_depth: 200 } });
      await until(() => !current.current.pending && lists().length === 2 && rows(1).length === 200, "second result");
      check("terminal result replaces buffered text", messages()[1].querySelector(".bubble").textContent === "FINAL second [200]");
      check("terminal sources refresh every field", rows(1)[199].textContent === "final-second source 200");
      check("terminal citation links to source 200", messages()[1].querySelector("a.cite").getAttribute("href").endsWith("/final-second-200.md"));
      lists()[0].querySelector("summary").click(); await paint();
      check("old list opens all 500 on first click", rows(0).length === 500);
      check("selection survives stop and new request", lists()[0].querySelectorAll(".source-select")[499].checked);

      await submit("Acceptance third", "full");
      await until(() => io.streams.length === 3, "third request");
      const third = io.streams[2];
      check("third request uses full mode", third.request.response_mode === "full");
      third.send({ type: "sources", sources: sources(167, "third") });
      await until(() => lists().length === 3 && rows(2).length === 167, "third sources");
      check("manual old opening survives another request", lists()[0].open && rows(0).length === 500);
      check("previous automatic list collapses", !lists()[1].open && rows(1).length === 0);
      lists()[2].querySelector("summary").click(); await paint();
      third.send({ type: "sources", sources: sources(167, "third") });
      third.send({ type: "result", data: { answer: "FINAL third [167]", sources: sources(167, "third") } });
      await until(() => !current.current.pending, "third result");
      check("manual latest collapse survives final sources", !lists()[2].open && rows(2).length === 0);
      lists()[2].querySelector("summary").click(); await paint();
      check("all 167 final sources remain accessible", rows(2).length === 167);
      check("all 867 sources retained across 6 messages", current.current.messages.length === 6
        && current.current.messages.filter((m) => m.role === "assistant").reduce((n, m) => n + m.sources.length, 0) === 867);
      save({ status: "PASS", checks: passed });
    } catch (error) { save({ status: "FAIL", error: error.message, passed }); }
    finally { transport.current?.restore(); transport.current = null; setRunning(false); }
  }
  function retained() {
    const ok = chat.messages.length === 6 && lists().length === 3 && rows(0).length === 500 && rows(1).length === 0
      && rows(2).length === 167 && lists()[0].querySelectorAll(".source-select")[499].checked;
    save({ ...report, navigation: ok ? "PASS" : "FAIL" }, false);
  }
  return <>
    <section className="panel" style={{ flex: "0 0 auto", overflow: "visible" }}>
      <h2>Real ChatPanel acceptance</h2>
      <p>Normal SSO and settings; deterministic NDJSON chat transport. Synthetic sources only.</p>
      <button onClick={checks} disabled={running}>Run ChatPanel checks</button>{" "}
      <Link href="/chat">Open normal chat</Link>{" "}
      <button onClick={retained} disabled={running || report.status !== "PASS"}>Check history after returning</button>
      <p data-testid="panel-summary">{previousResult && "Предыдущий результат · "}{report.status} · checks: {report.checks?.length ?? report.passed?.length ?? 0}
        {report.navigation && ` · navigation: ${report.navigation}`}</p>
      {report.completed_at && <p>Прогон завершён: <time dateTime={report.completed_at}>{report.completed_at}</time></p>}
      <pre data-testid="panel-report" style={{ maxHeight: 220, overflow: "auto" }}>{JSON.stringify(report, null, 2)}</pre>
    </section>
    <div ref={root} style={{ flex: "1 0 600px", minHeight: 600, display: "flex", flexDirection: "column" }}><ChatPanel /></div>
  </>;
}
