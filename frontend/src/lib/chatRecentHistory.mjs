export const EMPTY_RECENT_HISTORY = {turns: [], nextBeforeId: null, status: "idle", error: null};

// A loader belongs to one ChatContext owner. Late replies after reset/logout are ignored.
export function createRecentHistoryLoader({fetchPage, onChange, getSessionId}) {
  let currentSessionId = null;
  getSessionId ??= () => currentSessionId;
  let state = EMPTY_RECENT_HISTORY;
  let generation = 0;
  let disposed = false;
  const publish = next => { state = next; onChange(next); };
  return {
    setSessionId(value) { currentSessionId = value; },
    async load({older = false} = {}) {
      if (disposed || state.status === "loading" || state.status === "disabled") return;
      if (older ? !state.nextBeforeId : state.status === "ready") return;
      const token = generation;
      const beforeId = older ? state.nextBeforeId : null;
      publish({...state, status: "loading", error: null});
      try {
        const page = await fetchPage({limit: 10, beforeId, excludeSessionId: getSessionId()});
        if (disposed || generation !== token) return;
        const turns = new Map([...page.turns, ...(older ? state.turns : [])]
          .filter(turn => turn.session_id !== getSessionId()).map(turn => [turn.id, turn]));
        publish({turns: [...turns.values()].sort((a, b) => a.id - b.id),
          nextBeforeId: page.next_before_id, status: "ready", error: null});
        return page;
      } catch (error) {
        if (!disposed && generation === token) publish({...state, status: "error", error});
      }
    },
    clear() {
      generation++;
      publish({...EMPTY_RECENT_HISTORY, status: "disabled"});
    },
    // React Strict Mode may reconnect the same owner after an effect cleanup.
    activate() {
      if (!disposed) return;
      disposed = false;
      if (state.status === "loading") state = {...state, status: "idle"};
    },
    dispose() { generation++; disposed = true; },
  };
}

export function chatFeedTurns(history, messages, sessionId) {
  const turns = history.filter(turn => turn.session_id !== sessionId).map(turn => ({
    ...turn, key: `history:${turn.id}`, historical: true,
    messages: turn.messages.map(message => ({message})),
  }));
  for (const [index, message] of messages.entries()) {
    if (message.role === "user" || !turns.length || turns.at(-1).historical) {
      turns.push({key: `live:${index}`, historical: false, messages: []});
    }
    turns.at(-1).messages.push({message, index});
  }
  return turns;
}

export function estimatedTurnHeight(turn, width, height) {
  const columns = Math.max(20, Math.floor((width - 40) / 8));
  return turn.messages.reduce((total, {message}) => {
    const text = message.text ?? message.content ?? "";
    const lines = text.split("\n").reduce((sum, line) => sum + Math.max(1, Math.ceil(line.length / columns)), 0);
    return total + 52 + Math.min(lines * 23, height * .8) + (message.sources?.length ? 42 : 0);
  }, 12);
}

export function recentTurnStart(turns, {height = 400, width = 700, heights = {}}) {
  height = Math.max(160, height);
  let used = 0;
  let start = turns.length;
  while (start > 0) {
    const turn = turns[start - 1];
    const size = heights[turn.key] ?? estimatedTurnHeight(turn, width, height);
    if (start < turns.length && used + size > height * 2) break;
    used += size;
    start--;
  }
  return start;
}
