export function consumePopupEscape(event, open) {
  if (!open || event.key !== "Escape") return false;
  event.preventDefault();
  event.stopPropagation();
  return true;
}
