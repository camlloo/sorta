import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// 开发模式：npm run dev（Vite 5173）→ 代理 /api 到本地 FastAPI（uv run sorta ui --port 8502 --no-browser）
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8502",
        changeOrigin: true,
        // 开发代理自动携带后端会话 token（生产为同源，无需代理）
        configure: (proxy) => {
          proxy.on("proxyReq", (proxyReq) => {
            const token = sessionStorage.getItem("sorta_token") ?? "";
            if (token) proxyReq.setHeader("X-SORTA-Token", token);
          });
        },
      },
    },
  },
});
