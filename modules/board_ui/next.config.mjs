/** @type {import('next').NextConfig} */
const apiBase = process.env.ACF_API_BASE || "http://app:8000";

const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  async rewrites() {
    return [
      // Proxy all /api/* calls (including SSE /api/.../stream) to the Board API.
      { source: "/api/:path*", destination: `${apiBase}/:path*` },
    ];
  },
};

export default nextConfig;
