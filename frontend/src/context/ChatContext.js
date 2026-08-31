"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { getChatSettings } from "@/lib/api";

const FALLBACK_SETTINGS = {
  top_k_min: 1,
  top_k_max: 10,
  top_k_default: 5,
  top_k_presets: [4, 5, 10],
  search_mode_default: "hybrid",
  search_modes: ["dense", "bm25", "hybrid"],
};

const MODE_LABELS = {
  dense: "Семантический",
  bm25: "По ключевым словам (BM25)",
  hybrid: "Гибрид",
};

const ChatContext = createContext(null);

export function ChatProvider({ children }) {
  const [messages, setMessages] = useState([]);
  const [tags, setTags] = useState([]);
  const [pending, setPending] = useState(false);
  const [sessionId, setSessionId] = useState(null);
  const [settings, setSettings] = useState(FALLBACK_SETTINGS);
  const [selectedMode, setSelectedMode] = useState(FALLBACK_SETTINGS.search_mode_default);

  // «Новый чат»: сбрасывает ленту и UUID треда — следующее сообщение откроет
  // новую сессию истории (Этап 6).
  const startNewChat = () => {
    setMessages([]);
    setSessionId(null);
  };

  useEffect(() => {
    let cancelled = false;
    getChatSettings()
      .then((data) => {
        if (!cancelled) {
          const merged = { ...FALLBACK_SETTINGS, ...data };
          setSettings(merged);
          setSelectedMode((prev) => {
            const modes = merged.search_modes ?? [];
            return modes.includes(prev) ? prev : merged.search_mode_default;
          });
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <ChatContext.Provider value={{ messages, tags, pending, settings, selectedMode, sessionId, setSessionId, startNewChat, setMessages, setTags, setPending, setSelectedMode, MODE_LABELS }}>
      {children}
    </ChatContext.Provider>
  );
}

export function useChat() {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error("useChat must be used within ChatProvider");
  return ctx;
}
