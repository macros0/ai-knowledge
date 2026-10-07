import assert from 'node:assert/strict';
import test from 'node:test';
import * as scrolling from '../src/lib/chatScrollPosition.mjs';
const {attachChatScroll} = scrolling;

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

test('prepending older turns keeps the visible question at the same viewport offset', () => {
  assert.equal(typeof scrolling.captureChatViewport, 'function');
  const area = new ScrollArea();
  let questionTop = 300;
  area.getBoundingClientRect = () => ({top: 0});
  const question = {dataset: {chatTurn: 'history:4'}, getBoundingClientRect: () => ({top: questionTop - area.scrollTop, bottom: questionTop - area.scrollTop + 500})};
  area.querySelectorAll = () => [question];
  area.scrollTop = 320;
  const saved = scrolling.captureChatViewport(area);
  questionTop += 600;
  area.scrollHeight += 600;
  scrolling.restoreChatViewport(area, saved);
  assert.equal(area.scrollTop, 920);
  assert.equal(question.getBoundingClientRect().top, -20);
});

test('history resizing follows the bottom only when the reader was already there', () => {
  assert.equal(typeof scrolling.captureChatViewport, 'function');
  const area = new ScrollArea();
  area.getBoundingClientRect = () => ({top: 0});
  area.querySelectorAll = () => [];
  area.scrollTop = 1500;
  const saved = scrolling.captureChatViewport(area);
  area.scrollHeight = 2100;
  scrolling.restoreChatViewport(area, saved);
  assert.equal(area.scrollTop, 1800);
  area.scrollTop = 100;
  const reading = scrolling.captureChatViewport(area);
  area.scrollHeight = 2600;
  scrolling.restoreChatViewport(area, reading);
  assert.equal(area.scrollTop, 100);
});
