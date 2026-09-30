"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { getChatSettings } from "@/lib/api";
import { useI18n } from "@/i18n/LocaleContext";

const FALLBACK_SETTINGS = {
  knowledge_profile: null,
  top_k_min: 1,
  top_k_max: 10,
  top_k_default: 5,
  top_k_presets: [4, 5, 10],
  search_mode_default: "hybrid",
  search_modes: ["dense", "bm25", "hybrid"],
  response_modes: [],
  search_depth_default: 40,
  search_depth_min: 1,
  search_depth_max: 500,
  search_depth_presets: [40, 100, 200],
  glossary_query_expansion_enabled: null,
  bulk_export_enabled: false,
  bulk_export_download_enabled: false,
  bulk_export_max_docs: 1000,
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
  const [searchDepth, setSearchDepth] = useState(FALLBACK_SETTINGS.search_depth_default);
  const [mailMode, setMailMode] = useState("all");
  const [useGlossary, setUseGlossary] = useState(true);
  const [searchScopeDocuments, setSearchScopeDocuments] = useState([]);
  const [searchScopeEnabled, setSearchScopeEnabled] = useState(false);

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
    setSearchScopeDocuments([]);
    setSearchScopeEnabled(false);
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
    () => ({ messages, tags, pending, settings, selectedMode, searchDepth, setSearchDepth, sessionId, mailMode, setMailMode, useGlossary, setUseGlossary, setSessionId, startNewChat, setMessages, setTags, setPending, setSelectedMode, MODE_LABELS, searchScopeDocuments, setSearchScopeDocuments, searchScopeEnabled, setSearchScopeEnabled }),
    [messages, tags, pending, settings, selectedMode, searchDepth, sessionId, mailMode, useGlossary, MODE_LABELS, searchScopeDocuments, searchScopeEnabled]
  );

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

export function useChat() {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error("useChat must be used within ChatProvider");
  return ctx;
}
