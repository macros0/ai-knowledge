export function attachChatScroll(element, position, chat) {
  const saved = position.current;
  const returning = saved?.sessionId === chat.sessionId && saved?.messageCount === chat.messageCount;
  element.scrollTop = returning ? saved.top : element.scrollHeight;
  const save = () => { position.current = { ...chat, top: element.scrollTop }; };
  save();
  element.addEventListener('scroll', save, { passive: true });
  return () => {
    save();
    element.removeEventListener('scroll', save);
  };
}
