/** @type {import('next').NextConfig} */
module.exports = {
  output: "standalone",
  experimental: {
    proxyTimeout: 300_000,
  },
  async rewrites() {
    const backendUrl = process.env.BACKEND_URL || "http://localhost:8000";
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};
