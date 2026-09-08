"use client";

// Общий словарь тегов на страницу: один GET /api/tags на ВСЕ пикеры/комбобоксы,
// а не по запросу на каждый (при 300 карточках это были бы 300 вызовов).
// После любой мутации тегов (upload / PATCH / bulk-tags / удаление) вызывается
// bumpTagVersion() — кэш сбрасывается, все подписчики перечитывают справочник.
// Тот же bump вызывает смена языка интерфейса (LocaleContext.changeLocale):
// display-имена тегов локализуются бэкендом по cookie okf.locale, и без сброса
// подписчики держали бы справочник на языке до переключения.

import { useSyncExternalStore } from "react";
import { listTags } from "./api";

let cache = null; // null — не загружен; undefined — загружается; иначе — массив
let generation = 0; // растёт на каждой bump: ответы устаревшего поколения не пишутся
const listeners = new Set();

function emit() {
  for (const fn of listeners) fn();
}

function ensureLoaded() {
  if (cache === null) {
    cache = undefined;
    const gen = generation;
    listTags()
      .then((tags) => {
        // Инвалидация во время in-flight (смена языка/мутация до ответа) — ответ
        // старого поколения отбрасывается: кэш перечитают уже с новым поколением.
        if (gen === generation) cache = tags;
        emit();
      })
      .catch(() => {
        if (gen === generation) cache = [];
        emit();
      });
  }
}

export function bumpTagVersion() {
  generation += 1;
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