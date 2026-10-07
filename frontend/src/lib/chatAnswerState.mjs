import { applyAssessmentOutcome } from "./chatSourceAssessment.mjs";
import { reconcileChatSources } from "./chatRenderState.mjs";

export function sourcesForView(sources = [], view = "documents") {
  // Assign citation numbers before reordering, including older saved answers.
  const displaySources = sources.map((source, index) => ({
    ...source, display_index: source.source_index ?? index + 1,
  }));
  if (view === "rating") {
    displaySources.sort((a, b) => {
      const left = Number.isFinite(a.score) ? a.score : -Infinity;
      const right = Number.isFinite(b.score) ? b.score : -Infinity;
      return left === right ? 0 : right - left;
    });
  }
  return displaySources;
}

export function changeChatSourceView(messages, index, view) {
  if (!["documents", "rating"].includes(view)) return messages;
  const current = messages[index];
  if (!current || current.sourceView === view) return messages;
  const next = [...messages];
  next[index] = { ...current, sourceView: view };
  return next;
}

export function toggleChatSources(messages, index, open) {
  const current = messages[index];
  if (!current || (current.sourcesTouched && current.sourcesOpen === open)) return messages;
  const next = [...messages];
  next[index] = { ...current, sourcesOpen: open, sourcesTouched: true };
  return next;
}

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
    changed = { ...current, text: (current.text || "") + event.text, hasStreamedText: true };
  } else if (event.type === "sources") {
    const sources = reconcileChatSources(current.sources, event.sources || []);
    const sourcesOpen = current.sourcesTouched ? current.sourcesOpen : current.sourceAssessment?.decision === "reject" ? false : sources.length > 0;
    if (sources === current.sources && sourcesOpen === current.sourcesOpen) return messages;
    changed = {
      ...current,
      sources,
      sourcesOpen,
    };
  } else if (event.type === "progress") {
    changed = event.phase === "source_assessment"
      ? {...applyAssessmentOutcome(current, event.source_assessment ?? {status: event.status}), progress: event}
      : event.phase === "retrieval"
      ? { ...current, requestSearchDepth: event.search_depth, searchLimitReached: event.search_limit_reached }
      : { ...current, progress: event };
  } else {
    return messages;
  }
  const next = [...messages];
  next[index] = changed;
  return next;
}
