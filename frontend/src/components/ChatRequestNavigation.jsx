"use client";

import { useEffect, useState } from "react";
import { groupRequestMarkers, timelineRequestNavigation } from "@/lib/chatRequestNavigation.mjs";
import { useI18n } from "@/i18n/LocaleContext";

export default function ChatRequestNavigation({ turns, logRef }) {
  const { t } = useI18n();
  const [navigation, setNavigation] = useState({ markers: [], activeIndex: null });
  const [expandedGroup, setExpandedGroup] = useState(null);
  const turnKeys = turns.map(turn => turn.key).join(",");

  useEffect(() => {
    const log = logRef.current;
    if (!log) return;
    let frame;
    const measure = () => {
      setNavigation(timelineRequestNavigation(log));
    };
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(measure);
    };
    const observer = new ResizeObserver(schedule);
    observer.observe(log);
    if (log.firstElementChild) observer.observe(log.firstElementChild);
    log.addEventListener("scroll", schedule, { passive: true });
    schedule();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      log.removeEventListener("scroll", schedule);
    };
  }, [turnKeys, logRef]);

  if (!navigation.markers.length) return null;
  const groups = groupRequestMarkers(navigation.markers, Math.max(0, navigation.viewportHeight - 12));
  const ordinals = new Map(navigation.markers.map((marker, index) => [marker.index, index + 1]));
  const questions = new Map(turns.map(turn => {
    const message = turn.messages.find(item => item.message.role === "user")?.message;
    return [turn.key, message?.text ?? message?.content ?? ""];
  }));
  const requestLabel = (marker) => t("chat.jumpToRequest", { number: ordinals.get(marker.index), question: questions.get(marker.index) ?? "" });
  const jump = (marker) => {
    logRef.current?.scrollTo({ top: marker.target, behavior: "smooth" });
    setExpandedGroup(null);
  };
  return (
    <nav className="chat-request-navigation" aria-label={t("chat.requestNavigation")}
      onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setExpandedGroup(null); }}
      onKeyDown={(event) => { if (event.key === "Escape") setExpandedGroup(null); }}>
      {groups.map((group) => {
        const marker = group.markers[0];
        const grouped = group.markers.length > 1;
        const label = grouped ? t("chat.requestGroup", { first: ordinals.get(marker.index), last: ordinals.get(group.markers.at(-1).index) }) : requestLabel(marker);
        const active = group.markers.some((item) => navigation.activeIndex === item.index);
        return (
          <div key={marker.index} className="chat-request-marker-slot" style={{ top: `${group.position}%` }}>
          <button type="button"
            className={`chat-request-mark ${active ? "active" : ""} ${grouped ? "grouped" : ""}`}
            aria-label={label} title={label}
            aria-current={active ? "location" : undefined}
            aria-expanded={grouped ? expandedGroup === marker.index : undefined}
            onClick={() => grouped ? setExpandedGroup(expandedGroup === marker.index ? null : marker.index) : jump(marker)} />
          {grouped && expandedGroup === marker.index && (
            <div className="chat-request-menu" aria-label={label} style={group.position > 50 ? { bottom: 0 } : { top: 0 }}>
              {group.markers.map((item) => (
                <button key={item.index} type="button" title={requestLabel(item)}
                  aria-current={navigation.activeIndex === item.index ? "location" : undefined}
                  onClick={() => jump(item)}>{requestLabel(item)}</button>
              ))}
            </div>
          )}
          </div>
        );
      })}
    </nav>
  );
}
