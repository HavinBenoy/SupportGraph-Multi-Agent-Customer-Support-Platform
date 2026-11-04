/** The browser only ever talks to this origin; Next forwards /api/* to the FastAPI server. No CORS needed, and the API URL never reaches the client. */
const API_URL = process.env.SUPPORTGRAPH_API_URL || "http://127.0.0.1:8000";

export default {
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/api/:path*` }];
  },
  async headers() {
    return [{ source: "/:path*", headers: [
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "X-Frame-Options", value: "DENY" },
      { key: "Referrer-Policy", value: "same-origin" },
    ] }];
  },
};
