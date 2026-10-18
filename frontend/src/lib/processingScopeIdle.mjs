// Keep the public scope timer API while sharing the authoritative implementation.
export {EDITOR_IDLE_MS as SCOPE_EDITOR_IDLE_MS, EDITOR_WARNING_MS as SCOPE_EDITOR_WARNING_MS,
  createEditorIdleTimer as createScopeIdleTimer} from "./editorIdle.mjs";
