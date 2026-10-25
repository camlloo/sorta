# SORTA 前端

基于 React 19、TypeScript、Vite 8 和 Tailwind CSS 4，包含对话、文件库、历史和设置页面。项目介绍与界面预览见 [仓库 README](../README.md)。

## 环境与依赖

建议使用 Node.js 22.12 或以上。在本目录运行：

```powershell
npm ci
```

## 本地开发

先在仓库根目录启动后端：

```powershell
uv run sorta ui --no-browser
```

再在本目录启动前端：

```powershell
npm run dev
```

打开终端给出的本地地址，通常为 [http://127.0.0.1:5173](http://127.0.0.1:5173)。`/api` 请求默认代理到 `http://127.0.0.1:8502`；会话凭据由前端 API 客户端获取并通过请求头传递。

如果后端使用了其他端口，可在启动 Vite 前设置代理目标：

```powershell
$env:SORTA_API_URL = "http://127.0.0.1:8503"
npm run dev
```

## 检查与构建

```powershell
npm run lint
npm run build
```

构建结果位于 `dist/`。日常使用由 `uv run sorta ui` 同源托管，无需额外启动 Vite。
