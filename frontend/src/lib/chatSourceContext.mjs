export function inModelContext(source) {
  // История до появления признака содержит только источники контекста.
  return Boolean(source) && source.in_model_context !== false;
}
