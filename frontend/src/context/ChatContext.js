"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { getChatSettings } from "@/lib/api";
import { useI18n } from "@/i18n/LocaleContext";

const FALLBACK_SETTINGS = {
  top_k_min: 1,
  top_k_max: 10,
  top_k_default: 5,
  top_k_presets: [4, 5, 10],
  search_mode_default: "hybrid",
  search_modes: ["dense", "bm25", "hybrid"],
};

const ChatContext = createContext(null);

export function ChatProvider({ children }) {
  const { t } = useI18n();
  const [messages, setMessages] = useState([]);
  const [tags, setTags] = useState([]);
  const [pending, setPending] = useState(false);
  const [sessionId, setSessionId] = useState(null);
  const [settings, setSettings] = useState(FALLBACK_SETTINGS);
  const [selectedMode, setSelectedMode] = useState(FALLBACK_SETTINGS.search_mode_default);

  const MODE_LABELS = useMemo(
    () => ({
      dense: t("chat.modeDense"),
      bm25: t("chat.modeBm25"),
      hybrid: t("chat.modeHybrid"),
    }),
    [t]
  );

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

  const value = useMemo(
    () => ({ messages, tags, pending, settings, selectedMode, sessionId, setSessionId, startNewChat, setMessages, setTags, setPending, setSelectedMode, MODE_LABELS }),
    [messages, tags, pending, settings, selectedMode, sessionId, MODE_LABELS]
  );

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

export function useChat() {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error("useChat must be used within ChatProvider");
  return ctx;
}
