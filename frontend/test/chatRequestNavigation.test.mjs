import assert from 'node:assert/strict';
import test from 'node:test';

test('history and new questions share stable scroll marks based on the rendered timeline', async () => {
  const { timelineRequestNavigation } = await import('../src/lib/chatRequestNavigation.mjs');
  const elements = [
    {dataset: {chatTurn: 'history:31'}, getBoundingClientRect: () => ({top: -350})},
    {dataset: {chatTurn: 'live:0'}, getBoundingClientRect: () => ({top: 250})},
  ];
  const log = {scrollTop: 450, clientHeight: 400, scrollHeight: 1200,
    getBoundingClientRect: () => ({top: 100}),
    querySelectorAll: selector => {assert.equal(selector, '[data-chat-turn]'); return elements;},
  };
  const result = timelineRequestNavigation(log);
  assert.deepEqual(result.markers.map(({index, top, target}) => ({index, top, target})), [
    {index: 'history:31', top: 0, target: 0}, {index: 'live:0', top: 600, target: 600},
  ]);
  assert.equal(result.activeIndex, 'history:31');
  log.scrollTop = 800;
  assert.equal(timelineRequestNavigation(log).activeIndex, 'live:0');
});

test('request markers follow actual message positions and identify the visible request', async () => {
  const { requestNavigation } = await import('../src/lib/chatRequestNavigation.mjs');
  const requests = [{ index: 0, top: 0 }, { index: 2, top: 800 }, { index: 4, top: 1600 }];
  const result = requestNavigation(requests, 850, 400, 2000);
  assert.equal(result.activeIndex, 2);
  assert.deepEqual(result.markers.map(({ position, target }) => [position, target]), [[0, 0], [40, 800], [80, 1600]]);
  assert.equal(requestNavigation(requests, 1600, 400, 2000).activeIndex, 4);
});

test('dense request marks are grouped without hiding any question behind another hit target', async () => {
  const { groupRequestMarkers } = await import('../src/lib/chatRequestNavigation.mjs');
  const markers = [{ index: 0, position: 0 }, { index: 2, position: 0.4 }, { index: 4, position: 0.8 }, { index: 6, position: 20 }];
  const groups = groupRequestMarkers(markers, 488);
  assert.deepEqual(groups.map(group => group.markers.map(marker => marker.index)), [[0, 2, 4], [6]]);
  assert.deepEqual(groups.flatMap(group => group.markers), markers);
  assert.ok((groups[1].position - groups[0].position) / 100 * 488 >= 12);
});

test('short chats, empty chats and the last request within a long answer have safe positions', async () => {
  const { requestNavigation } = await import('../src/lib/chatRequestNavigation.mjs');
  assert.deepEqual(requestNavigation([], 0, 400, 0), { markers: [], activeIndex: null });
  const short = requestNavigation([{ index: 0, top: 0 }, { index: 2, top: 150 }], 0, 400, 300);
  assert.deepEqual(short.markers.map(({ target }) => target), [0, 0]);
  assert.equal(short.activeIndex, 0);
  const long = requestNavigation([{ index: 0, top: 0 }, { index: 2, top: 700 }], 2600, 400, 3000);
  assert.equal(long.activeIndex, 2);
  assert.ok(long.markers.every(({ position }) => position >= 0 && position <= 100));
  const lastNearEnd = requestNavigation([{ index: 0, top: 0 }, { index: 2, top: 1900 }], 1600, 400, 2000);
  assert.equal(lastNearEnd.markers[1].target, 1600);
  assert.equal(lastNearEnd.activeIndex, 2);
});
