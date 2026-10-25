import React, { useCallback, useEffect, useState } from "react";
import { NavLink, Route, Routes, useNavigate } from "react-router-dom";
import {
  api,
  bootstrapSession,
  type FileRow,
  type SessionInfo,
  type Settings,
} from "./services/api";
import ChatPage from "./pages/ChatPage";
import FilesPage from "./pages/FilesPage";
import PackPage from "./pages/PackPage";
import HistoryPage from "./pages/HistoryPage";
import SettingsPage from "./pages/SettingsPage";

export interface WorkspaceState {
  scanId: string | null;
  root: string;
  fileCount: number;
  hasMore: boolean;
  skippedDirectories: string[];
  files: FileRow[];
  model: string;
  hasApiKey: boolean;
  privacy: string;
  settingsLoaded: boolean;
}

interface WorkspaceContextValue extends WorkspaceState {
  setWorkspace: (info: { scan_id: string; root: string; file_count: number }) => void;
  refreshFiles: (scanId: string) => Promise<void>;
  clearWorkspace: () => void;
  refreshSettings: () => Promise<void>;
}

const WorkspaceContext = React.createContext<WorkspaceContextValue | null>(null);

// eslint-disable-next-line react-refresh/only-export-components
export function useWorkspace(): WorkspaceContextValue {
  const ctx = React.useContext(WorkspaceContext);
  if (!ctx) throw new Error("useWorkspace 必须在 WorkspaceProvider 内使用");
  return ctx;
}

export default function App() {
  const [state, setState] = useState<WorkspaceState>({
    scanId: localStorage.getItem("sorta_scan_id"),
    root: localStorage.getItem("sorta_root") ?? "",
    fileCount: 0,
    hasMore: false,
    skippedDirectories: [],
    files: [],
    model: "",
    hasApiKey: false,
    privacy: "authorized_content",
    settingsLoaded: false,
  });
  const [bootError, setBootError] = useState("");
  const [booted, setBooted] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    bootstrapSession()
      .then((info: SessionInfo) => {
        setState((s) => ({ ...s, model: info.model, hasApiKey: info.has_api_key }));
        return api.getSettings();
      })
      .then((settings: Settings) => {
        setState((s) => ({
          ...s,
          root: s.root || settings.root,
          privacy: settings.privacy,
          settingsLoaded: true,
        }));
        setBooted(true);
      })
      .catch((err: Error) => {
        setBootError(err.message);
        setBooted(true);
      });
  }, []);

  const refreshFiles = useCallback(async (scanId: string) => {
    const data = await api.listFiles(scanId);
    setState((s) => ({ ...s, scanId, files: data.files, fileCount: data.count }));
  }, []);

  const setWorkspace = useCallback(
        (info: {
          scan_id: string;
          root: string;
          file_count: number;
          has_more?: boolean;
          skipped_directories?: string[];
        }) => {
      localStorage.setItem("sorta_scan_id", info.scan_id);
      localStorage.setItem("sorta_root", info.root);
      setState((s) => ({
        ...s,
        scanId: info.scan_id,
        root: info.root,
        fileCount: info.file_count,
        hasMore: info.has_more ?? false,
        skippedDirectories: info.skipped_directories ?? [],
      }));
      void refreshFiles(info.scan_id);
    },
    [refreshFiles],
  );

  const clearWorkspace = useCallback(() => {
    localStorage.removeItem("sorta_scan_id");
    setState((s) => ({ ...s, scanId: null, files: [], fileCount: 0 }));
  }, []);

  const refreshSettings = useCallback(async () => {
    const settings = await api.getSettings();
    setState((s) => ({
      ...s,
      root: settings.root,
      privacy: settings.privacy,
      model: settings.model,
      hasApiKey: settings.has_api_key,
      settingsLoaded: true,
    }));
  }, []);

  // 启动时若存在已持久化的扫描会话，尝试恢复文件列表；过期则清除
  useEffect(() => {
    if (state.scanId && state.settingsLoaded) {
      refreshFiles(state.scanId).catch(() => {
        localStorage.removeItem("sorta_scan_id");
        setState((s) => ({ ...s, scanId: null, files: [], fileCount: 0 }));
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.settingsLoaded]);

  return (
    <WorkspaceContext.Provider
      value={{ ...state, setWorkspace, refreshFiles, clearWorkspace, refreshSettings }}
    >
      <div className="flex h-full">
        <aside className="flex w-[220px] shrink-0 flex-col border-r border-neutral-200">
          <div className="px-6 py-6">
            <div className="text-xl font-bold tracking-tight">SORTA</div>
            <div className="mt-0.5 text-[10px] tracking-[0.2em] text-neutral-400">
              AI FILE ORGANIZER
            </div>
          </div>
          <nav className="flex flex-col gap-1 px-3">
            {[
              { to: "/", label: "对话", icon: "💬" },
              { to: "/files", label: "文件库", icon: "🗂️" },
              { to: "/pack", label: "交付", icon: "📦" },
              { to: "/history", label: "历史", icon: "📜" },
              { to: "/settings", label: "设置", icon: "⚙️" },
            ].map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/"}
                className={({ isActive }) =>
                  `rounded-md px-3 py-2 text-sm ${
                    isActive
                      ? "bg-neutral-100 font-medium text-neutral-900"
                      : "text-neutral-600 hover:bg-neutral-50"
                  }`
                }
              >
                <span className="mr-2">{item.icon}</span>
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="mt-auto px-6 py-4 text-xs text-neutral-500">
            {state.scanId ? (
              <>
                <div className="font-medium text-neutral-800">
                  🟢 已索引 {state.fileCount} 个文件
                </div>
                <div className="mt-1 truncate text-neutral-400" title={state.root}>
                  {state.root}
                </div>
              </>
            ) : (
              <>
                <div>⚪ 未扫描目录</div>
                <button
                  className="mt-1 text-blue-600 hover:underline"
                  onClick={() => navigate("/settings")}
                >
                  在「设置」中选择工作目录
                </button>
              </>
            )}
          </div>
        </aside>
        <main className="min-w-0 flex-1 overflow-y-auto">
          {!booted ? (
            <div className="p-8 text-sm text-neutral-400">正在连接 SORTA 服务…</div>
          ) : bootError ? (
            <div className="p-8 text-red-600">无法连接 SORTA 服务：{bootError}</div>
          ) : (
            <Routes>
              <Route path="/" element={<ChatPage />} />
              <Route path="/files" element={<FilesPage />} />
              <Route path="/pack" element={<PackPage />} />
              <Route path="/history" element={<HistoryPage />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Routes>
          )}
        </main>
      </div>
    </WorkspaceContext.Provider>
  );
}
