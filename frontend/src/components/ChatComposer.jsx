"use client";

import { memo, useState } from "react";
import { useI18n } from "@/i18n/LocaleContext";

function ChatComposer({ disabled, onSubmit }) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");

  const submit = async (event) => {
    event.preventDefault();
    const question = query.trim();
    if (!question || disabled) return;
    setQuery("");
    await onSubmit(question);
  };

  return (
    <form className="chat-form" onSubmit={submit}>
      <input value={query} onChange={(event) => setQuery(event.target.value)}
        placeholder={t("chat.queryPlaceholder")} autoComplete="off" />
      <button type="submit" disabled={disabled}>{t("chat.send")}</button>
    </form>
  );
}

export default memo(ChatComposer);
