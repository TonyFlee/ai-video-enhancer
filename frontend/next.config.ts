import type { NextConfig } from "next";

const API_TARGET = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

// The packaged desktop app has no Node server: FastAPI serves a statically
// exported build of this frontend on the same origin as /api. Enable that build
// with NEXT_EXPORT=1 (see backend/scripts/build_windows_app.ps1). Without it we
// keep the dev experience: `next dev` proxies /api to FastAPI.
const isExport = process.env.NEXT_EXPORT === "1";

type ExperimentalOptions = NonNullable<NextConfig["experimental"]>;

const nextConfig: NextConfig = {
  ...(isExport
    ? {
        // Static export: everything is prerendered to HTML/JS that FastAPI serves.
        // PPR and the dev-only Next.js /api proxy are not available here.
        output: "export" as const,
        partialPrefetching: false,
      }
    : {
        cacheComponents: true,
        partialPrefetching: true,
        // The Next.js proxy buffers request bodies in memory (default 10 MB) before
        // forwarding them to FastAPI, which silently truncates larger uploads and breaks
        // /api/probe + /api/jobs for real video files. Match the backend's upload limit.
        experimental: {
          // Env values are plain `string`; the literal is validated by Next at load time.
          proxyClientMaxBodySize: (process.env.AVE_PROXY_MAX_BODY ??
            "4gb") as ExperimentalOptions["proxyClientMaxBodySize"],
        },
        async rewrites() {
          // Proxy API + uploaded/enhanced media through the Next.js origin so the browser
          // never talks to the FastAPI port directly (no CORS, no mixed-origin media).
          return [
            { source: "/api/:path*", destination: `${API_TARGET}/api/:path*` },
          ];
        },
      }),
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
