import type { NextConfig } from "next";

const apiUrl = process.env.API_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  poweredByHeader: false,
  // The browser talks to the API through this site, so the session cookie is
  // first-party and no cross-origin rules are needed.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
};

export default config;
