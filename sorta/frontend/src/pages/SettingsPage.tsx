import { useEffect, useState } from "react";
import { useWorkspace } from "../App";
import { api } from "../services/api";

/** 示意图 ③ 设置页：工作目录授权 + 隐私模式 + 模型状态。 */

export default function SettingsPage() {
  const workspace = useWorkspace();
  const [root, setRoot] = useState(workspace.root);
  const [privacy, setPrivacy] = useState(workspace.privacy);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setRoot(workspace.root);
    setPrivacy(workspace.privacy);
  }, [workspace.root, workspace.privacy]);

  async function saveAndScan() {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await api.saveSettings({ root, privacy });
      const info = await api.createWorkspace(root);
      workspace.setWorkspace(info);
      await workspace.refreshSettings();
      setMessage(`已保存并索引 ${info.file_count} 个文件。`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="h-full overflow-y-auto px-8 py-6">
      <h1 className="text-xl font-bold">设置</h1>

      <h2 className="mt-6 text-sm font-bold">工作目录</h2>
      <label className="mt-2 block text-xs text-neutral-500">授权根目录</label>
      <input
        className="mt-1 w-full max-w-xl rounded border border-neutral-300 px-3 py-2 text-sm focus:border-neutral-500 focus:outline-none"
        placeholder="例如 D:\Downloads"
        value={root}
        onChange={(e) => setRoot(e.target.value)}
      />
      <div className="mt-4">
        <div className="text-xs text-neutral-500">隐私模式（发送给云端模型的内容）</div>
        {[
          { value: "metadata_only", label: "仅元数据（不发送文件内容）" },
          { value: "authorized_content", label: "授权读取内容（文件文本将发送给云端模型）" },
        ].map((option) => (
          <label key={option.value} className="mt-2 flex items-center gap-2 text-sm">
            <input
              type="radio"
              name="privacy"
              checked={privacy === option.value}
              onChange={() => setPrivacy(option.value)}
            />
            {option.label}
          </label>
        ))}
        <p className="mt-2 text-xs text-neutral-400">
          metadata_only：只发送文件名与元数据；authorized_content：发送限长文本提取。
          所有文件操作仅限该目录内，执行前必须经你确认。
        </p>
      </div>
      <button
        className="mt-4 rounded bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        disabled={busy || root.trim() === ""}
        onClick={saveAndScan}
      >
        {busy ? "扫描中…" : "保存并扫描"}
      </button>

      <h2 className="mt-8 text-sm font-bold">模型与接口</h2>
      {workspace.hasApiKey ? (
        <div className="mt-2 rounded bg-green-50 px-3 py-2 text-xs text-green-700">
          模型：`{workspace.model || "未返回"}` · 接口：本地配置（.env）
        </div>
      ) : (
        <div className="mt-2 rounded bg-red-50 px-3 py-2 text-xs text-red-700">
          未配置模型：请在安装目录的 .env 中填写 SORTA_MODEL 与 SORTA_API_KEY。
        </div>
      )}
      <p className="mt-2 text-xs text-neutral-400">
        扫描会话有效期 1 小时；所有文件操作仅限授权目录内，执行前必须经你确认；
        数据保存在本机 SQLite，不上传。
      </p>

      {message && (
        <div className="mt-4 max-w-xl rounded bg-neutral-100 px-3 py-2 text-xs">{message}</div>
      )}
      {error && (
        <div className="mt-4 max-w-xl rounded bg-red-50 px-3 py-2 text-xs text-red-700">
          {error}
        </div>
      )}
    </div>
  );
}
