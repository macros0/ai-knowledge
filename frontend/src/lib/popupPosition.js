"use client";

// Позиционирование портальных попапов (position:fixed) с защитой от ухода за
// экран: при нехватке места снизу — разворот вверх, плюс кламп по горизонтали.
// Вызывается из useLayoutEffect (до отрисовки) — без видимого «прыжка».

export function positionPopup(triggerEl, popupEl, { gap = 4, margin = 8, width = null } = {}) {
  const r = triggerEl.getBoundingClientRect();
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  if (width != null) popupEl.style.width = `${width}px`;
  const w = width ?? popupEl.offsetWidth;

  let left = r.left;
  if (left + w > vw - margin) left = Math.max(margin, vw - margin - w);
  popupEl.style.left = `${left}px`;

  const h = popupEl.offsetHeight;
  let top = r.bottom + gap;
  if (top + h > vh - margin) {
    top = r.top - gap - h; // разворот вверх
    if (top < margin) top = Math.max(margin, vh - margin - h); // кламп в границы
  }
  popupEl.style.top = `${top}px`;
}
