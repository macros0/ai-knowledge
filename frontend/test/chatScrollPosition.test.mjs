import assert from 'node:assert/strict';
import test from 'node:test';
import { attachChatScroll } from '../src/lib/chatScrollPosition.mjs';

// The browser owns layout and clamps scrollTop; EventTarget supplies real events.
class ScrollArea extends EventTarget {
  scrollHeight = 1800;
  clientHeight = 300;
  #top = 0;
  get scrollTop() { return this.#top; }
  set scrollTop(value) { this.#top = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); }
}

const chat = { sessionId: 'chat-a', messageCount: 4 };

test('returning from a concept restores the position, including the very top', () => {
  for (const top of [320, 0]) {
    const position = { current: null };
    const first = new ScrollArea();
    const leave = attachChatScroll(first, position, chat);
    first.scrollTop = top;
    // Cleanup must capture a final position even before a scroll event fires.
    leave();
    const returned = new ScrollArea();
    const leaveAgain = attachChatScroll(returned, position, chat);
    assert.equal(returned.scrollTop, top);
    leaveAgain();
  }
});

test('scrolling is saved but streaming text cannot pull the viewport to the end', () => {
  const position = { current: null };
  const area = new ScrollArea();
  const leave = attachChatScroll(area, position, chat);
  area.scrollTop = 240;
  area.dispatchEvent(new Event('scroll'));
  assert.equal(position.current.top, 240);
  area.scrollHeight = 2400;
  assert.equal(area.scrollTop, 240);
  leave();
  area.scrollTop = 900;
  area.dispatchEvent(new Event('scroll'));
  assert.equal(position.current.top, 240); // Listener removed on navigation.
});

test('new messages or a different chat start at the end instead of using an old position', () => {
  for (const nextChat of [{ ...chat, messageCount: 6 }, { ...chat, sessionId: 'chat-b' }]) {
    const position = { current: { ...chat, top: 320 } };
    const area = new ScrollArea();
    const leave = attachChatScroll(area, position, nextChat);
    assert.equal(area.scrollTop, 1500);
    leave();
  }
});
