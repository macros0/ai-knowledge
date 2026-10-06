"use client";
import {memo,useLayoutEffect,useRef} from "react";
import {useI18n} from "@/i18n/LocaleContext";
import {shouldSubmitQuestion} from "@/lib/chatComposer.mjs";
function ChatComposer({value,onChange,disabled,onSubmit}) {
  const {t}=useI18n();
  const input=useRef(null);
  useLayoutEffect(()=>{
    const el=input.current;
    if(!el) return;
    el.style.height="auto";
    const lineHeight=parseFloat(getComputedStyle(el).lineHeight)||22;
    el.style.height=`${Math.min(el.scrollHeight,lineHeight*8+24)}px`;
  },[value]);
  const submit=event=>{
    event.preventDefault();
    const question=value.trim();
    if(question && !disabled) onSubmit(question);
  };
  return <form className="chat-form" onSubmit={submit}>
    <div className="chat-compose-input">
      <textarea id="chat-question" ref={input} rows={1} value={value} onChange={e=>onChange(e.target.value)}
        aria-label={t("ux.questionLabel")} aria-describedby="chat-key-hint" placeholder={t("chat.queryPlaceholder")} autoComplete="off"
        onKeyDown={e=>{if(e.nativeEvent.keyCode !== 229 && shouldSubmitQuestion({key:e.key,shiftKey:e.shiftKey,isComposing:e.nativeEvent.isComposing})) submit(e);}} />
      <span id="chat-key-hint" className="meta">{t("ux.questionKeys")}</span>
    </div>
    <button type="submit" disabled={disabled || !value.trim()}>{t("chat.send")}</button>
  </form>;
}
export default memo(ChatComposer);
