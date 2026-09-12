"use client";

import { useChat } from "@/context/ChatContext";
import { useI18n } from "@/i18n/LocaleContext";

export default function KnowledgeProfile() {
  const { settings } = useChat();
  const { t } = useI18n();
  const profile = settings?.knowledge_profile;
  const glossaryEnabled = settings?.glossary_query_expansion_enabled;
  const glossaryStatus = glossaryEnabled === true
    ? t("nav.glossaryEnabled")
    : glossaryEnabled === false
      ? t("nav.glossaryDisabled")
      : t("nav.glossaryUnknown");

  if (!profile) return null;

  return (
    <span className="knowledge-profile" role="status" title={t("nav.knowledgeProfileTitle")}>
      <span className="knowledge-profile-name">{profile}</span>
      <span className="knowledge-profile-glossary">{glossaryStatus}</span>
    </span>
  );
}
