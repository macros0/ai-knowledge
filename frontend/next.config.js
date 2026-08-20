/** @type {import('next').NextConfig} */
module.exports = {
  output: "standalone",
  devIndicators: false,
  experimental: {
    proxyTimeout: 300_000,
  },
  async rewrites() {
    const backendUrl = process.env.BACKEND_URL || "http://127.0.0.1:8000";
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};
