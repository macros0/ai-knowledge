"use client";

import {useCallback, useEffect, useLayoutEffect, useRef, useState} from "react";
import Link from "next/link";
import {useI18n} from "@/i18n/LocaleContext";
import {recentTurnStart} from "@/lib/chatRecentHistory.mjs";
import {captureChatViewport, restoreChatViewport} from "@/lib/chatScrollPosition.mjs";
import {fmtDate, HistoryMessage} from "./ChatHistoryShared";
import ChatRequestNavigation from "./ChatRequestNavigation";

function TimelineTurn({turn, previewHeight, expanded, onToggle, onInteract, renderMessage, pending}) {
  const {t} = useI18n();
  const contentRef = useRef(null);
  const pointerFocusRef = useRef(false);
  useEffect(() => {
    const release = () => {pointerFocusRef.current = false;};
    window.addEventListener("pointerup", release, true);
    window.addEventListener("pointercancel", release, true);
    return () => {
      window.removeEventListener("pointerup", release, true);
      window.removeEventListener("pointercancel", release, true);
    };
  }, []);
  const [overflow, setOverflow] = useState(false);
  useLayoutEffect(() => {
    const node = contentRef.current;
    const measure = () => setOverflow(node.scrollHeight > previewHeight + 24);
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    measure();
    return () => observer.disconnect();
  }, [previewHeight]);
  const collapsed = overflow && !expanded && !pending;
  const question = turn.messages.find(({message}) => message.role === "user")?.message;
  const questionText = question?.text ?? question?.content ?? "";
  return <article className="chat-timeline-turn" data-chat-turn={turn.key}>
    {turn.historical && <div className="chat-history-date meta">
      <span>{t("chat.recentHistory")} · {fmtDate(turn.created_at)}</span>
      <Link href={`/chat/history?session=${encodeURIComponent(turn.session_id)}`}>{t("chat.openHistoryThread")}</Link>
    </div>}
    <div className={`chat-turn-preview ${!expanded && !pending ? "limited" : ""} ${collapsed ? "collapsed" : ""}`}
      onPointerDownCapture={() => {pointerFocusRef.current = true; onInteract();}}
      onPointerUpCapture={() => {pointerFocusRef.current = false;}}
      onPointerCancelCapture={() => {pointerFocusRef.current = false;}}
      onFocusCapture={() => {if (collapsed && !pointerFocusRef.current) onToggle(); else onInteract();}}
      onClickCapture={collapsed ? onToggle : onInteract}
      style={{"--chat-preview-height": `${previewHeight}px`}}>
      <div ref={contentRef}>
        {turn.messages.map(({message, index}, i) => turn.historical
          ? <HistoryMessage key={i} m={message}/>
          : renderMessage(message, index))}
      </div>
    </div>
    {overflow && !pending && <button type="button" className="btn ghost chat-turn-expand"
      aria-expanded={!collapsed} aria-label={`${t(collapsed ? "chat.expandDialog" : "chat.collapseDialog")}: ${questionText.slice(0, 180)}`}
      onClick={onToggle}>{t(collapsed ? "chat.expandDialog" : "chat.collapseDialog")}</button>}
  </article>;
}

export default function ChatTimeline({turns, pending, recentHistory, onLoadOlder, renderMessage, empty,
  logRef, scrollPositionRef, sessionId, messageCount, view, setView}) {
  const {t} = useI18n();
  const contentRef = useRef(null);
  const [layout, setLayout] = useState(() => view.layout ?? {height: 400, width: 700, heights: {}});
  const layoutRef = useRef(layout);
  const autoStart = recentTurnStart(turns, layout);
  const requestedStart = view.startKey == null ? -1 : turns.findIndex(turn => turn.key === view.startKey);
  const start = requestedStart < 0 ? autoStart : Math.min(autoStart, requestedStart);
  const visible = turns.slice(start);
  const visibleKeys = visible.map(turn => turn.key).join(",");
  const remember = useCallback(() => {
    if (logRef.current) scrollPositionRef.current = {
      ...captureChatViewport(logRef.current), sessionId, messageCount,
    };
  }, [logRef, scrollPositionRef, sessionId, messageCount]);

  useLayoutEffect(() => {
    const log = logRef.current;
    const saved = scrollPositionRef.current;
    const sameChat = saved?.sessionId === sessionId;
    restoreChatViewport(log, sameChat ? saved : null, {forceBottom: saved?.messageCount !== messageCount});
    remember();
    log.addEventListener("scroll", remember, {passive: true});
    return () => { log.removeEventListener("scroll", remember); };
  }, [visibleKeys, layout.height, layout.width, view.expanded, sessionId, messageCount, logRef, scrollPositionRef, remember]);

  useLayoutEffect(() => {
    const log = logRef.current;
    const measure = () => {
      const previous = layoutRef.current;
      const width = log.clientWidth;
      const height = log.clientHeight;
      const heights = previous.width === width ? {...previous.heights} : {};
      for (const node of contentRef.current.querySelectorAll("[data-chat-turn]")) {
        heights[node.dataset.chatTurn] = node.getBoundingClientRect().height + 12;
      }
      const next = {width, height, heights};
      if (JSON.stringify(previous) !== JSON.stringify(next)) {
        layoutRef.current = next;
        setLayout(next);
        setView(current => ({...current, layout: next}));
      }
      // Growing streaming text follows the bottom but does not interrupt a reader above it.
      restoreChatViewport(log, scrollPositionRef.current);
      remember();
    };
    const observer = new ResizeObserver(measure);
    observer.observe(log);
    observer.observe(contentRef.current);
    measure();
    return () => { observer.disconnect(); };
  }, [logRef, scrollPositionRef, remember, visibleKeys, setView]);

  const rememberReader = () => {
    remember();
    if (scrollPositionRef.current) scrollPositionRef.current = {...scrollPositionRef.current, atBottom: false};
  };
  const loadPage = async (older) => {
    remember();
    if (scrollPositionRef.current) scrollPositionRef.current = {...scrollPositionRef.current, atBottom: !older};
    const page = await onLoadOlder({older});
    if (older && page?.turns.length) setView(current => ({...current, startKey: `history:${page.turns[0].id}`}));
  };
  const loadPrevious = async () => {
    rememberReader();
    if (start > 0) {
      const previous = turns.slice(0, start);
      const earlier = recentTurnStart(previous, layout);
      setView(current => ({...current, startKey: previous[earlier].key}));
    } else {
      await loadPage(true);
    }
  };
  const olderAvailable = start > 0 || recentHistory.nextBeforeId != null;
  const renderedTurns = visible.map(turn => <TimelineTurn key={turn.key} turn={turn}
    previewHeight={Math.max(160, layout.height * .8)} expanded={view.expanded[turn.key]}
    pending={pending && turn.key === turns.at(-1)?.key} renderMessage={renderMessage}
    onInteract={() => {
      rememberReader();
      setView(current => current.startKey === visible[0]?.key ? current : {...current, startKey: visible[0]?.key});
    }}
    onToggle={() => {
      rememberReader();
      setView(current => ({...current, startKey: visible[0]?.key,
        expanded: {...current.expanded, [turn.key]: !current.expanded[turn.key]}}));
    }}/>);

  return <><div className="chat-log" ref={logRef} tabIndex={0} role="region" aria-label={t("chat.title")}>
    <div className="chat-log-content" ref={contentRef}>
      {olderAvailable && <button type="button" className="btn ghost chat-history-previous"
        disabled={recentHistory.status === "loading"} onClick={loadPrevious}>{t("chat.showPrevious")}</button>}
      {recentHistory.status === "loading" && <p className="meta" role="status">{t("chat.loadingRecentHistory")}</p>}
      {recentHistory.status === "error" && <div className="chat-history-load-error" role="status">
        <span>{t("chat.recentHistoryError")}</span>
        <button type="button" className="btn ghost" onClick={() => loadPage(recentHistory.nextBeforeId != null)}>{t("common.retry")}</button>
      </div>}
      {turns.length === 0 && recentHistory.status !== "loading" && empty}
      {renderedTurns}
    </div>
  </div><ChatRequestNavigation turns={visible} logRef={logRef}/></>;
}
