import React, { useCallback, useEffect, useRef, useState } from "react";
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
  setWorkspace: (info: {
    scan_id: string;
    root: string;
    file_count: number;
    has_more?: boolean;
    skipped_directories?: string[];
  }) => void;
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

const NAV = [
  { to: "/", label: "对话" },
  { to: "/files", label: "文件库" },
  { to: "/history", label: "历史" },
];

function GearIcon({ size = 18 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
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
  const fileRequest = useRef(0);

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
    const requestId = ++fileRequest.current;
    const data = await api.listFiles(scanId);
    if (requestId !== fileRequest.current) return;
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
        files: [],
        hasMore: info.has_more ?? false,
        skippedDirectories: info.skipped_directories ?? [],
      }));
      void refreshFiles(info.scan_id).catch(() => undefined);
    },
    [refreshFiles],
  );

  const clearWorkspace = useCallback(() => {
    fileRequest.current += 1;
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
      const savedScanId = state.scanId;
      refreshFiles(savedScanId).catch(async (err: Error & { status?: number }) => {
        if (localStorage.getItem("sorta_scan_id") !== savedScanId) return;
        // 快照过期可以对已授权目录重新扫描；网络瞬断不能清掉工作区和会话。
        if (err.status === 410) {
          try {
            const info = await api.rescanWorkspace(savedScanId);
            if (localStorage.getItem("sorta_scan_id") === savedScanId) setWorkspace(info);
          } catch (scanError) {
            if ((scanError as { status?: number }).status === 400 ||
                (scanError as { status?: number }).status === 410) clearWorkspace();
          }
        }
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.settingsLoaded]);

  return (
    <WorkspaceContext.Provider
      value={{ ...state, setWorkspace, refreshFiles, clearWorkspace, refreshSettings }}
    >
      <div className="flex h-full flex-col md:flex-row">
        {/* 左栏：品牌 / 导航 / 索引计数器。当前项靠 3px 信号红条 + 字重声明。 */}
        <aside className="flex w-full shrink-0 items-center border-b border-rule md:w-[220px] md:flex-col md:items-stretch md:border-r md:border-b-0">
          <div className="px-4 py-3 md:px-6 md:pt-8 md:pb-6">
            <div
              className="text-[28px] leading-none font-extrabold tracking-[-0.04em]"
              style={{ fontWeight: 850 }}
            >
              SORTA
            </div>
            <div className="mt-2 hidden font-mono text-[10px] tracking-[0.18em] text-graphite md:block">
              AI FILE ORGANIZER
            </div>
            <div className="mt-1 hidden font-mono text-[10px] tracking-[0.18em] text-graphite md:block">
              / LOCAL
            </div>
          </div>

          <nav className="flex md:flex-col" aria-label="主导航">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/"}
                className={({ isActive }) =>
                  `group relative border-rule px-3 py-3 text-[15px] transition-colors duration-100 md:border-b md:pr-4 md:pl-6 ${
                    isActive
                      ? "bg-ink font-semibold text-paper"
                      : "font-medium text-ink hover:bg-ink hover:text-paper"
                  }`
                }
              >
                {({ isActive }) => (
                  <>
                    <span
                      className={`absolute top-0 bottom-0 left-0 w-[3px] transition-opacity duration-100 ${
                        isActive ? "bg-signal opacity-100" : "opacity-0"
                      }`}
                      aria-hidden
                    />
                    {item.label}
                  </>
                )}
              </NavLink>
            ))}
          </nav>

          {/* 索引计数器：大 mono 数字是文件库状态的客观陈述 */}
          <div className="mt-auto hidden px-6 pb-4 md:block">
            <div className="eyebrow">已索引</div>
            {state.scanId ? (
              <>
                <div className="mt-1 font-mono text-[34px] leading-none font-semibold tracking-tight tabular-nums">
                  {state.fileCount}
                  <span className="ml-1 text-[12px] font-normal text-graphite">个文件</span>
                </div>
                {state.hasMore && (
                  <div className="mt-1 font-mono text-[10px] text-caution">+ 还有更多未加载</div>
                )}
                {state.skippedDirectories.length > 0 && (
                  <div
                    className="mt-1 font-mono text-[10px] text-caution"
                    title={state.skippedDirectories.join("、")}
                  >
                    ! 跳过 {state.skippedDirectories.length} 个目录
                  </div>
                )}
                <div className="mt-2 truncate font-mono text-[10px] text-graphite" title={state.root}>
                  {state.root}
                </div>
              </>
            ) : (
              <>
                <div className="mt-1 font-mono text-[34px] leading-none font-semibold text-rule-strong">
                  —
                </div>
                <button
                  className="mt-2 text-left text-[12px] text-signal underline-offset-2 hover:underline"
                  onClick={() => navigate("/settings")}
                >
                  选择工作目录 →
                </button>
              </>
            )}
          </div>

          {/* 设置：左下角图标按钮（线性齿轮，hover 反色，active 红条） */}
          <NavLink
            to="/settings"
            aria-label="设置"
            title="设置"
            className={({ isActive }) =>
              `group relative ml-auto flex h-12 w-12 shrink-0 items-center justify-center border-rule transition-colors duration-100 md:ml-0 md:border-t ${
                isActive ? "bg-ink text-paper" : "text-graphite hover:bg-ink hover:text-paper"
              }`
            }
          >
            {({ isActive }) => (
              <>
                <span
                  className={`absolute top-0 bottom-0 left-0 w-[3px] transition-opacity duration-100 ${
                    isActive ? "bg-signal opacity-100" : "opacity-0"
                  }`}
                  aria-hidden
                />
                <GearIcon />
              </>
            )}
          </NavLink>
        </aside>

        <main className="min-h-0 min-w-0 flex-1 overflow-y-auto">
          {!booted ? (
            <div className="p-8 font-mono text-xs text-graphite">正在连接 SORTA 服务…</div>
          ) : bootError ? (
            <div className="border-b-2 border-signal p-8">
              <div className="eyebrow">连接失败</div>
              <p className="mt-2 text-sm">{bootError}</p>
              <p className="mt-1 text-xs text-graphite">
                请确认服务已启动：uv run sorta ui
              </p>
            </div>
          ) : (
            <Routes>
              <Route path="/" element={<ChatPage />} />
              <Route path="/files" element={<FilesPage />} />
              <Route path="/history" element={<HistoryPage />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Routes>
          )}
        </main>
      </div>
    </WorkspaceContext.Provider>
  );
}
