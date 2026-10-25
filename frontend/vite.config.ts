import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// 开发模式：npm run dev（Vite 5173）→ 代理 /api 到本地 FastAPI（uv run sorta ui --port 8502 --no-browser）
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api": {
        target: process.env.SORTA_API_URL || "http://127.0.0.1:8502",
        changeOrigin: true,
        // 浏览器 API 客户端携带会话 token；代理直接转发请求头。
      },
    },
  },
});
