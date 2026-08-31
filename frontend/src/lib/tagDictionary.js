"use client";

// Общий словарь тегов на страницу: один GET /api/tags на ВСЕ пикеры/комбобоксы,
// а не по запросу на каждый (при 300 карточках это были бы 300 вызовов).
// После любой мутации тегов (upload / PATCH / bulk-tags / удаление) вызывается
// bumpTagVersion() — кэш сбрасывается, все подписчики перечитывают справочник.

import { useSyncExternalStore } from "react";
import { listTags } from "./api";

let cache = null; // null — не загружен; undefined — загружается; иначе — массив
const listeners = new Set();

function emit() {
  for (const fn of listeners) fn();
}

function ensureLoaded() {
  if (cache === null) {
    cache = undefined;
    listTags()
      .then((tags) => {
        cache = tags;
        emit();
      })
      .catch(() => {
        cache = [];
        emit();
      });
  }
}

export function bumpTagVersion() {
  cache = null;
  emit();
}

function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function useTagDictionary() {
  ensureLoaded();
  const snapshot = useSyncExternalStore(
    subscribe,
    () => cache,
    () => []
  );
  return snapshot ?? [];
}