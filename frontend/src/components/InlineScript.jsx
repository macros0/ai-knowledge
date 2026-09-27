"use client";

// Hydration-safe инлайн-скрипт (гайд Next.js «preventing flash before hydration»):
// на сервере — type=text/javascript (браузер выполняет при парсинге HTML, до
// гидратации — анти-FOUC темы/<html lang>); на клиенте — type=text/plain, чтобы
// React 19 не предупреждал «Encountered a script tag while rendering React
// component» и не пытался выполнить скрипт повторно. suppressHydrationWarning
// гасит расхождение type между SSR и клиентом.
// nonce — из CSP ответа (src/proxy.js). Браузер скрывает атрибут nonce после
// разбора документа, расхождение при гидратации гасит тот же
// suppressHydrationWarning.
export default function InlineScript({ html, nonce }) {
  return (
    <script
      type={typeof window === "undefined" ? "text/javascript" : "text/plain"}
      nonce={nonce}
      suppressHydrationWarning
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
