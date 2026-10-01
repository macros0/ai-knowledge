import assert from 'node:assert/strict';
import test from 'node:test';

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
