/** @type {import('next').NextConfig} */
module.exports = {
  output: "standalone",
  devIndicators: false,
  experimental: {
    proxyTimeout: 300_000,
    // Прокси-лимит тела (API route → backend). Дефолт 10MB обрезал upload-файлы
    // с сетевого ресурса. Ключ актуален для Next 16.3.1 (переименован из
    // middlewareClientMaxBodySize при миграции middleware → proxy).
    // При апгрейде Next перепроверить секцию body-size лимитов в changelog.
    proxyClientMaxBodySize: "100mb",
  },
  async rewrites() {
    const backendUrl = process.env.BACKEND_URL || "http://127.0.0.1:18000";
    return [
      {
        source: "/health",
        destination: `${backendUrl}/health`,
      },
    ];
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};
