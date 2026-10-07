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

export function captureChatViewport(element, previous = null) {
  const origin = element.getBoundingClientRect().top;
  const first = [...element.querySelectorAll("[data-chat-turn]")]
    .find(node => node.getBoundingClientRect().bottom > origin);
  return {top: element.scrollTop,
    // A layout measurement at the same scroll offset must not undo an explicit
    // reader position just because the unexpanded content currently fits.
    atBottom: previous?.atBottom === false && previous.top === element.scrollTop
      ? false
      : element.scrollHeight - element.clientHeight - element.scrollTop <= 4,
    anchor: first ? {key: first.dataset.chatTurn, offset: first.getBoundingClientRect().top - origin} : null};
}

export function restoreChatViewport(element, saved, {forceBottom = false} = {}) {
  if (!saved || saved.atBottom || forceBottom) {
    element.scrollTop = element.scrollHeight;
    return;
  }
  const anchor = [...element.querySelectorAll("[data-chat-turn]")]
    .find(node => node.dataset.chatTurn === saved.anchor?.key);
  element.scrollTop = anchor
    ? element.scrollTop + anchor.getBoundingClientRect().top - element.getBoundingClientRect().top - saved.anchor.offset
    : saved.top;
}
