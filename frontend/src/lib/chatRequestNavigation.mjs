export function timelineRequestNavigation(log) {
  const origin = log.getBoundingClientRect().top;
  const requests = Array.from(log.querySelectorAll("[data-chat-turn]"), element => ({
    index: element.dataset.chatTurn,
    top: element.getBoundingClientRect().top - origin + log.scrollTop,
  }));
  return {...requestNavigation(requests, log.scrollTop, log.clientHeight, log.scrollHeight), viewportHeight: log.clientHeight};
}

export function requestNavigation(requests, scrollTop, viewportHeight, contentHeight) {
  const maxScroll = Math.max(0, contentHeight - viewportHeight);
  const markers = requests.map((request) => ({
    ...request,
    position: Math.max(0, Math.min(100, request.top / Math.max(1, contentHeight) * 100)),
    target: Math.max(0, Math.min(maxScroll, request.top)),
  }));
  let activeIndex = markers[0]?.index ?? null;
  for (const marker of markers) {
    if (marker.top <= scrollTop + 24) activeIndex = marker.index;
  }
  if (maxScroll > 0 && scrollTop >= maxScroll - 2 && markers.length) {
    activeIndex = markers.at(-1).index;
  }
  return { markers, activeIndex };
}

export function groupRequestMarkers(markers, railHeight) {
  const groups = [];
  for (const marker of markers) {
    const previous = groups.at(-1);
    if (previous && (marker.position - previous.position) / 100 * railHeight < 12) {
      previous.markers.push(marker);
    } else {
      groups.push({ position: marker.position, markers: [marker] });
    }
  }
  return groups;
}
