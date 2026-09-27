export async function readChatStream(response, onText = () => {}) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      pending += decoder.decode(value, { stream: !done });
      let newline;
      while ((newline = pending.indexOf("\n")) >= 0) {
        const line = pending.slice(0, newline).trim();
        pending = pending.slice(newline + 1);
        if (!line) continue;
        const event = JSON.parse(line);
        if (event.type === "delta") onText(event.text);
        if (event.type === "result") return event.data;
        if (event.type === "error") {
          const error = new Error("Chat failed");
          error.code = event.code || "internal_error";
          error.status = event.status || 503;
          throw error;
        }
      }
      if (done) throw new Error("Incomplete chat stream");
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
