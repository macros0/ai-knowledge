"use client";

import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
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
  const scrollPositionRef = useRef(null);
  const [settings, setSettings] = useState(FALLBACK_SETTINGS);
  const [selectedMode, setSelectedMode] = useState(FALLBACK_SETTINGS.search_mode_default);
  const [responseMode, setResponseMode] = useState("fast");
  const [selectedTopK, setSelectedTopK] = useState(null);
  const [showCustom, setShowCustom] = useState(false);
  const [customValue, setCustomValue] = useState("");
  const [showCustomDepth, setShowCustomDepth] = useState(false);
  const [customDepthValue, setCustomDepthValue] = useState("");
  const [searchSettingsOpen, setSearchSettingsOpen] = useState(false);
  // Эти фильтры и режимы живут вместе с чатом, включая переходы к концептам.
  // Модуль и разработка взаимоисключающие: их объединение в backend работает как OR.
  const [moduleFilter, setModuleFilter] = useState("");
  const [devFilter, setDevFilter] = useState(null);
  const [sourceLocale, setSourceLocale] = useState("");
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
    scrollPositionRef.current = null;
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
    () => ({ messages, tags, pending, settings, selectedMode, searchDepth, setSearchDepth, sessionId, scrollPositionRef, mailMode, setMailMode, useGlossary, setUseGlossary, setSessionId, startNewChat, setMessages, setTags, setPending, setSelectedMode, MODE_LABELS, searchScopeDocuments, setSearchScopeDocuments, searchScopeEnabled, setSearchScopeEnabled,
      responseMode, setResponseMode, selectedTopK: selectedTopK ?? settings.top_k_default, setSelectedTopK,
      showCustom, setShowCustom, customValue, setCustomValue, showCustomDepth, setShowCustomDepth, customDepthValue, setCustomDepthValue,
      moduleFilter, setModuleFilter, devFilter, setDevFilter, sourceLocale, setSourceLocale, searchSettingsOpen, setSearchSettingsOpen }),
    [messages, tags, pending, settings, selectedMode, searchDepth, sessionId, mailMode, useGlossary, MODE_LABELS, searchScopeDocuments, searchScopeEnabled,
      responseMode, selectedTopK, showCustom, customValue, showCustomDepth, customDepthValue, moduleFilter, devFilter, sourceLocale, searchSettingsOpen]
  );

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

export function useChat() {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error("useChat must be used within ChatProvider");
  return ctx;
}
