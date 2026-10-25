import { useCallback, useEffect, useState } from "react";
import { useWorkspace } from "../App";
import {
  api,
  formatEpoch,
  formatSize,
  type FileRow,
  type HistoryEntry,
} from "../services/api";

/** 示意图 ③ 文件库：索引表 + 搜索 + 详情/预览/总结 + 重复检测 + 历史撤销。 */

export default function FilesPage() {
  const workspace = useWorkspace();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ status: string; content: string | null; error: string | null } | null>(null);
  const [summary, setSummary] = useState("");
  const [dupGroups, setDupGroups] = useState<string[][] | null>(null);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [undoChoice, setUndoChoice] = useState<string | null>(null);

  const refreshHistory = useCallback(async () => {
    if (!workspace.scanId) return;
    try {
      const data = await api.history();
      setHistory(data.executions);
    } catch {
      /* 历史为空或会话过期时忽略 */
    }
  }, [workspace.scanId]);

  useEffect(() => {
    setSelected(null);
    setPreview(null);
    setSummary("");
    setDupGroups(null);
    void refreshHistory();
  }, [workspace.scanId, refreshHistory]);

  if (!workspace.scanId) {
    return (
      <div className="p-8">
        <h1 className="text-xl font-bold">文件库</h1>
        <p className="mt-4 text-sm text-neutral-500">
          尚未扫描工作目录：请在「设置」中选择目录并扫描，或在「对话」页直接发起整理。
        </p>
      </div>
    );
  }

  const files: FileRow[] = workspace.files.filter((f) =>
    query.trim() ? f.path.toLowerCase().includes(query.trim().toLowerCase()) : true,
  );
  const selectedFile = files.find((f) => f.path === selected) ?? null;

  async function doPreview() {
    if (!selectedFile || !workspace.scanId) return;
    setBusy("preview");
    setMessage("");
    try {
      setPreview(await api.previewFile(workspace.scanId, selectedFile.file_id));
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function doSummarize() {
    if (!selectedFile || !workspace.scanId) return;
    setBusy("summarize");
    setMessage("");
    try {
      const data = await api.summarizeFile(workspace.scanId, selectedFile.file_id);
      setSummary(data.summary);
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function doDuplicates() {
    if (!workspace.scanId) return;
    setBusy("dup");
    setMessage("");
    try {
      const data = await api.findDuplicates(workspace.scanId);
      setDupGroups(data.groups);
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function doUndo() {
    if (!undoChoice) return;
    setBusy("undo");
    setMessage("");
    try {
      const result = await api.undo(undoChoice);
      setMessage(
        result.status === "undone"
          ? `已全部撤销（${result.operations.length} 项）`
          : `部分撤销：${result.operations.filter((o) => o.status === "undone").length}/${result.operations.length} 项成功，冲突项已保留现场`,
      );
      if (workspace.scanId) void workspace.refreshFiles(workspace.scanId);
      void refreshHistory();
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="h-full overflow-y-auto px-8 py-6">
      <h1 className="text-xl font-bold">文件库</h1>
      <p className="mt-1 text-xs text-neutral-500">
        已索引 {workspace.fileCount} 个文件（仅当前授权目录的扫描结果，不做后台全盘索引）
      </p>

      <input
        className="mt-4 w-full max-w-md rounded border border-neutral-300 px-3 py-2 text-sm focus:border-neutral-500 focus:outline-none"
        placeholder="搜索文件名，例如：postman、笔记、pdf"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />

      <div className="mt-4 overflow-hidden rounded border border-neutral-200">
        <table className="w-full text-left text-sm">
          <thead className="bg-neutral-50 text-xs text-neutral-500">
            <tr>
              <th className="px-3 py-2 font-medium">路径</th>
              <th className="px-3 py-2 font-medium">类型</th>
              <th className="px-3 py-2 font-medium">大小</th>
              <th className="px-3 py-2 font-medium">修改时间</th>
            </tr>
          </thead>
          <tbody>
            {files.map((f) => (
              <tr
                key={f.file_id}
                className={`cursor-pointer border-t border-neutral-100 hover:bg-neutral-50 ${
                  selected === f.path ? "bg-blue-50" : ""
                }`}
                onClick={() => {
                  setSelected(f.path);
                  setPreview(null);
                  setSummary("");
                }}
              >
                <td className="px-3 py-1.5 font-mono text-xs">{f.path}</td>
                <td className="px-3 py-1.5 text-xs text-neutral-500">
                  .{f.extension || "-"}
                </td>
                <td className="px-3 py-1.5 text-xs text-neutral-500">{formatSize(f.size)}</td>
                <td className="px-3 py-1.5 text-xs text-neutral-500">
                  {formatEpoch(f.modified_ns / 1e9)}
                </td>
              </tr>
            ))}
            {files.length === 0 && (
              <tr>
                <td colSpan={4} className="px-3 py-4 text-center text-xs text-neutral-400">
                  没有匹配的文件
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {selectedFile && (
        <div className="mt-6 max-w-3xl rounded border border-neutral-200 p-4">
          <h2 className="text-sm font-bold">文件详情</h2>
          <p className="mt-1 font-mono text-xs text-neutral-500">{selectedFile.path}</p>
          <div className="mt-3 flex gap-2">
            <button
              className="rounded border border-neutral-300 px-3 py-1.5 text-xs disabled:opacity-50"
              disabled={busy !== ""}
              onClick={doPreview}
            >
              本地内容预览（不发送云端）
            </button>
            <button
              className="rounded bg-neutral-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
              disabled={busy !== ""}
              onClick={doSummarize}
            >
              📄 让 SORTA 总结此文件
            </button>
          </div>
          {preview && (
            <div className="mt-3">
              {preview.status === "ok" ? (
                <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap rounded bg-neutral-50 p-3 text-xs">
                  {preview.content}
                </pre>
              ) : (
                <p className="text-xs text-amber-600">无法提取文本：{preview.error}</p>
              )}
            </div>
          )}
          {summary && (
            <div className="mt-3 rounded bg-blue-50 px-3 py-2 text-xs text-blue-800 whitespace-pre-wrap">
              {summary}
            </div>
          )}
        </div>
      )}

      <div className="mt-6 max-w-3xl rounded border border-neutral-200 p-4">
        <h2 className="text-sm font-bold">重复文件检测</h2>
        <p className="mt-1 text-xs text-neutral-500">
          按内容 SHA-256 比对；SORTA 只负责列出，不会自动删除。
        </p>
        <button
          className="mt-2 rounded border border-neutral-300 px-3 py-1.5 text-xs disabled:opacity-50"
          disabled={busy !== ""}
          onClick={doDuplicates}
        >
          {busy === "dup" ? "比对中…" : "✨ 查找重复文件"}
        </button>
        {dupGroups !== null && (
          <div className="mt-3 space-y-1 text-xs">
            {dupGroups.length === 0 ? (
              <p className="text-neutral-500">未发现重复文件。</p>
            ) : (
              <>
                <p className="text-neutral-700">
                  发现 {dupGroups.length} 组重复文件（共{" "}
                  {dupGroups.reduce((n, g) => n + g.length, 0)} 个，内容完全相同）：
                </p>
                {dupGroups.map((group, i) => (
                  <p key={i}>
                    <span className="font-semibold">组 {i + 1}</span>：
                    {group.map((p) => (
                      <span key={p} className="mr-2 font-mono text-neutral-600">
                        {p}
                      </span>
                    ))}
                  </p>
                ))}
              </>
            )}
          </div>
        )}
      </div>

      {message && (
        <div className="mt-4 max-w-3xl rounded bg-neutral-100 px-3 py-2 text-xs">{message}</div>
      )}

      <div className="mt-6 max-w-3xl rounded border border-neutral-200 p-4">
        <h2 className="text-sm font-bold">执行历史与撤销</h2>
        {history.length === 0 ? (
          <p className="mt-1 text-xs text-neutral-500">暂无执行历史。</p>
        ) : (
          <>
            <table className="mt-2 w-full text-left text-xs">
              <thead className="text-neutral-500">
                <tr>
                  <th className="py-1 font-medium">批次</th>
                  <th className="py-1 font-medium">状态</th>
                  <th className="py-1 font-medium">开始</th>
                  <th className="py-1 font-medium">结束</th>
                </tr>
              </thead>
              <tbody>
                {history.map((e) => (
                  <tr key={e.execution_id} className="border-t border-neutral-100">
                    <td className="py-1 font-mono">{e.execution_id}</td>
                    <td className="py-1">{e.status}</td>
                    <td className="py-1 text-neutral-500">{formatEpoch(e.started_at)}</td>
                    <td className="py-1 text-neutral-500">{formatEpoch(e.finished_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="mt-2 flex gap-2">
              <select
                className="flex-1 rounded border border-neutral-300 px-2 py-1.5 text-xs"
                value={undoChoice ?? ""}
                onChange={(e) => setUndoChoice(e.target.value || null)}
              >
                <option value="">选择要撤销的批次…</option>
                {history
                  .filter((e) => e.status === "completed" || e.status === "partial_failed")
                  .map((e) => (
                    <option key={e.execution_id} value={e.execution_id}>
                      {e.execution_id}（{e.status}）
                    </option>
                  ))}
              </select>
              <button
                className="rounded border border-neutral-300 px-3 py-1.5 text-xs disabled:opacity-50"
                disabled={!undoChoice || busy !== ""}
                onClick={doUndo}
              >
                撤销该批次
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
