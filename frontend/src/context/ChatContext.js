"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import {readAssessmentPreference, writeAssessmentPreference} from "@/lib/chatSourceAssessment.mjs";
import { useAuth } from "./AuthContext";
import { getChatSettings, listRecentChatTurns } from "@/lib/api";
import {createRecentHistoryLoader, EMPTY_RECENT_HISTORY} from "@/lib/chatRecentHistory.mjs";
import { useI18n } from "@/i18n/LocaleContext";

const FALLBACK_SETTINGS = {
  source_assessment: {available: false, default_enabled: false, sample_size: 5},
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
  const {user,mode}=useAuth();
  const owner=`${mode}:${user?.user_id ?? user?.username ?? "anonymous"}`;
  return <ChatState key={owner}>{children}</ChatState>;
}

function ChatState({ children }) {
  const { t } = useI18n();
  const [messages, setMessages] = useState([]);
  const [queuedAssessmentRetry, setQueuedAssessmentRetry] = useState(null);
  const [sourceView, setSourceView] = useState("documents");
  const [draftQuery,setDraftQuery]=useState("");
  const [tags, setTags] = useState([]);
  const [pending, setPending] = useState(false);
  const [sessionId, setSessionId] = useState(null);
  const [recentHistory, setRecentHistory] = useState(EMPTY_RECENT_HISTORY);
  const [timelineView, setTimelineView] = useState({startKey: null, expanded: {}});
  const [historyLoader] = useState(() => createRecentHistoryLoader({
    fetchPage: listRecentChatTurns, onChange: setRecentHistory,
  }));
  useEffect(() => { historyLoader.setSessionId(sessionId); }, [historyLoader, sessionId]);
  useEffect(() => {
    historyLoader.activate();
    return () => historyLoader.dispose();
  }, [historyLoader]);
  const scrollPositionRef = useRef(null);
  const [settings, setSettings] = useState(FALLBACK_SETTINGS);
  const [assessSources, setAssessmentValue] = useState(null);
  const assessmentChosen = useRef(false);
  const setAssessSources = useCallback((enabled) => {
    assessmentChosen.current = true;
    setAssessmentValue(enabled);
    try { writeAssessmentPreference(window.localStorage, enabled); } catch { /* Storage access may throw. */ }
  }, []);
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
  const startNewChat = useCallback(() => {
    scrollPositionRef.current = null;
    setMessages([]);
    historyLoader.clear();
    setTimelineView({startKey: null, expanded: {}});
    setDraftQuery("");
    setSessionId(null);
    setSearchScopeDocuments([]);
    setSearchScopeEnabled(false);
  }, [historyLoader]);

  useEffect(() => {
    let cancelled = false;
    getChatSettings()
      .then((data) => {
        if (!cancelled) {
          const merged = { ...FALLBACK_SETTINGS, ...data };
          setSettings(merged);
          if (!assessmentChosen.current) {
            let preference = merged.source_assessment.default_enabled;
            try { preference = readAssessmentPreference(window.localStorage, preference); } catch { /* Use server default. */ }
            setAssessmentValue(preference);
          }
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
    () => ({queuedAssessmentRetry, setQueuedAssessmentRetry, assessSources: assessSources ?? settings.source_assessment.default_enabled, setAssessSources, recentHistory, historyLoader, timelineView, setTimelineView, sourceView,setSourceView,draftQuery,setDraftQuery,messages, tags, pending, settings, selectedMode, searchDepth, setSearchDepth, sessionId, scrollPositionRef, mailMode, setMailMode, useGlossary, setUseGlossary, setSessionId, startNewChat, setMessages, setTags, setPending, setSelectedMode, MODE_LABELS, searchScopeDocuments, setSearchScopeDocuments, searchScopeEnabled, setSearchScopeEnabled,
      responseMode, setResponseMode, selectedTopK: selectedTopK ?? settings.top_k_default, setSelectedTopK,
      showCustom, setShowCustom, customValue, setCustomValue, showCustomDepth, setShowCustomDepth, customDepthValue, setCustomDepthValue,
      moduleFilter, setModuleFilter, devFilter, setDevFilter, sourceLocale, setSourceLocale, searchSettingsOpen, setSearchSettingsOpen }),
    [queuedAssessmentRetry, assessSources, setAssessSources, recentHistory, historyLoader, timelineView, startNewChat, sourceView, draftQuery, messages, tags, pending, settings, selectedMode, searchDepth, sessionId, mailMode, useGlossary, MODE_LABELS, searchScopeDocuments, searchScopeEnabled,
      responseMode, selectedTopK, showCustom, customValue, showCustomDepth, customDepthValue, moduleFilter, devFilter, sourceLocale, searchSettingsOpen]
  );

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

export function useChat() {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error("useChat must be used within ChatProvider");
  return ctx;
}
