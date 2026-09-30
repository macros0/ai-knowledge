export function searchLimitWarning(reached, sourceCount, depth, maxDepth = 500) {
  // The backend flag also covers capped candidates before merging and filtering.
  // Only a full result list warrants advising the user to raise its limit.
  if (!reached || sourceCount < depth) return null;
  return depth >= maxDepth ? "chat.searchLimitReachedMax" : "chat.searchLimitReached";
}

export function groupSourcesByDocument(sources = []) {
  const groups = [];
  const byId = new Map();
  for (const source of sources) {
    const id = source.doc_id || source.filepath || source.title;
    if (!byId.has(id)) {
      const group = {
        doc_id: id,
        filename: source.filename || source.title,
        source,
        sources: [],
      };
      byId.set(id, group);
      groups.push(group);
    }
    byId.get(id).sources.push(source);
  }
  return groups;
}

export function applyAnswerEvent(messages, event) {
  const index = messages.length - 1;
  const current = messages[index];
  if (!current || current.role !== "assistant" || current.attemptId !== event.attemptId) {
    return messages;
  }
  let changed;
  if (event.type === "delta") {
    changed = { ...current, text: (current.text || "") + event.text };
  } else if (event.type === "sources") {
    changed = {
      ...current,
      sources: event.sources || [],
      sourcesOpen: current.sourcesTouched ? current.sourcesOpen : (event.sources || []).length > 0,
    };
  } else if (event.type === "progress") {
    changed = event.phase === "retrieval"
      ? { ...current, requestSearchDepth: event.search_depth, searchLimitReached: event.search_limit_reached }
      : { ...current, progress: event };
  } else {
    return messages;
  }
  const next = [...messages];
  next[index] = changed;
  return next;
}
