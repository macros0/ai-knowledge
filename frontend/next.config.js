/** @type {import('next').NextConfig} */
module.exports = {
  output: "standalone",
  devIndicators: false,
  experimental: {
    proxyTimeout: 300_000,
    // Прокси-лимит тела (rewrites → backend). Дефолт 10MB обрезал upload-файлы
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
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};
