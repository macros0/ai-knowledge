"use client";

import {useState} from "react";
import {useI18n} from "@/i18n/LocaleContext";
import ChatSearchScope from "./ChatSearchScope";
import Modal from "./Modal";

export default function ChatScopeSelector({documents, enabled, onEnabledChange, onRemove, onClear, refreshKey}) {
  const {t} = useI18n();
  const [editing, setEditing] = useState(false);
  return <>
    <select className="chat-scope-select" value={enabled ? "selected" : "all"} aria-label={t("chat.scopeLabel")}
      onChange={event => {
        if (event.target.value === "edit") setEditing(true);
        else onEnabledChange(event.target.value === "selected");
      }}>
      <option value="all">{t("ux.scopeAll")}</option>
      <option value="selected">{t("chat.scopeSelected", {count:documents.length})}</option>
      <option value="edit" disabled={!documents.length}>{t("chat.scopeEdit")}</option>
    </select>
    {editing && <Modal title={t("chat.scopeEditList")} onClose={() => setEditing(false)}
      footer={<button type="button" className="modal-btn" onClick={() => setEditing(false)}>{t("common.close")}</button>}>
      <ChatSearchScope documents={documents} onRemove={onRemove} onClear={onClear} refreshKey={refreshKey} expanded />
    </Modal>}
  </>;
}
