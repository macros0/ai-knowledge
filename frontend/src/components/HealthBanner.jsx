"use client";

import { useEffect, useState } from "react";
import { getHealth } from "@/lib/api";

const STATUS_TEXT = {
  ok: "Все сервисы работают нормально",
  degraded: "Один из сервисов недоступен — возможны ограничения",
  down: "База знаний недоступна — поиск не работает",
};

const DEP_LABELS = {
  llm: "LLM (OpenRouter)",
  ollama: "Ollama (эмбеддинги)",
  qdrant: "Qdrant (база знаний)",
};

export default function HealthBanner() {
  const [health, setHealth] = useState(null);

  useEffect(() => {
    let cancelled = false;
    let timer = null;

    const check = async () => {
      try {
        const data = await getHealth();
        if (!cancelled) setHealth(data);
      } catch {
        if (!cancelled) setHealth(null);
      }
    };

    check();
    timer = setInterval(check, 30000);

    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, []);

  if (!health || health.status === "ok") return null;

  // Показываем только реальный outage ("down"). Статус "rate_limited" (429 от
  // провайдера) намеренно НЕ отображается вообще — это не ошибка, а мягкий
  // троттлинг. Если когда-нибудь понадобится мягкое инфо-сообщение «ответы могут
  // идти медленнее» (без алерта), добавить отдельную ветку по
  // `v.status === "rate_limited"` и новый вариант баннера (не "degraded").
  const downDeps = Object.entries(health.dependencies || {})
    .filter(([, v]) => v.status === "down")
    .map(([k]) => DEP_LABELS[k] || k);

  const message = `${STATUS_TEXT[health.status] || ""}${downDeps.length ? ": " + downDeps.join(", ") : ""}`;

  return (
    <div className={`health-banner ${health.status}`}>
      {message}
    </div>
  );
}
