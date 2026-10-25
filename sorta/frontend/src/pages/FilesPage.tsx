import { useEffect, useState } from "react";
import { useWorkspace } from "../App";
import { api, formatEpoch, formatSize, type FileRow } from "../services/api";

/** 示意图 ③ 文件库（UX-03/04）：索引表 + 搜索 + 详情/预览/总结（隐私门）+
 * 重复检测（覆盖范围）+ 扫描范围提示与重新扫描。历史已独立为「历史」页。 */

export default function FilesPage() {
  const workspace = useWorkspace();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [preview, setPreview] = useState<{
    status: string;
    content: string | null;
    error: string | null;
  } | null>(null);
  const [summary, setSummary] = useState("");
  const [dupGroups, setDupGroups] = useState<string[][] | null>(null);
  const [dupScope, setDupScope] = useState<{ checked: number; skipped: number } | null>(null);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    setSelected(null);
    setPreview(null);
    setSummary("");
    setDupGroups(null);
    setDupScope(null);
  }, [workspace.scanId]);

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

  async function rescan() {
    if (!workspace.scanId) return;
    setBusy("rescan");
    setMessage("");
    try {
      const info = await api.rescanWorkspace(workspace.scanId);
      workspace.setWorkspace(info);
      setMessage(
        `已重新扫描：${info.file_count} 个文件` +
          (info.has_more ? `（还有更多未加载）` : "") +
          (info.skipped_directories.length
            ? `；跳过目录：${info.skipped_directories.join("、")}`
            : ""),
      );
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setBusy("");
    }
  }

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

  async function doSummarize(confirm = false) {
    if (!selectedFile || !workspace.scanId) return;
    setBusy("summarize");
    setMessage("");
    setNotice("");
    try {
      const data = await api.summarizeFile(workspace.scanId, selectedFile.file_id, confirm);
      setSummary(data.summary);
    } catch (err) {
      const apiErr = err as Error & { code?: string };
      if (apiErr.code === "CONSENT_REQUIRED") {
        // UX-03：ask_before_content 单次授权——展示文件、原因与上限后请求确认
        const confirmed = window.confirm(`${apiErr.message}\n\n是否允许本次读取？`);
        if (confirmed) {
          await doSummarize(true);
          return;
        }
        setMessage("已取消：未授权读取正文，总结未执行。");
      } else {
        setMessage(apiErr.message);
      }
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
      setDupScope({ checked: data.checked, skipped: data.skipped });
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
        已加载 {workspace.fileCount} 个文件
        {workspace.hasMore && (
          <span className="text-amber-600"> · 还有更多文件未加载（可点击重新扫描）</span>
        )}
        {workspace.skippedDirectories.length > 0 && (
          <span className="text-amber-600">
            {" "}
            · 跳过目录：{workspace.skippedDirectories.join("、")}（权限不足或规则跳过）
          </span>
        )}
      </p>

      <div className="mt-3 flex items-center gap-2">
        <input
          className="w-full max-w-md rounded border border-neutral-300 px-3 py-2 text-sm focus:border-neutral-500 focus:outline-none"
          placeholder="搜索文件名，例如：postman、笔记、pdf"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button
          className="rounded border border-neutral-300 px-3 py-2 text-xs disabled:opacity-50"
          disabled={busy !== ""}
          onClick={rescan}
        >
          {busy === "rescan" ? "重新扫描中…" : "↻ 重新扫描"}
        </button>
      </div>

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
                <td className="px-3 py-1.5 text-xs text-neutral-500">.{f.extension || "-"}</td>
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
              onClick={() => doSummarize(false)}
            >
              {busy === "summarize" ? "总结中…" : "📄 让 SORTA 总结此文件"}
            </button>
          </div>
          {notice && <p className="mt-2 text-xs text-neutral-500">{notice}</p>}
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
            <div className="mt-3 whitespace-pre-wrap rounded bg-blue-50 px-3 py-2 text-xs text-blue-800">
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
        {dupGroups !== null && dupScope !== null && (
          <div className="mt-3 space-y-1 text-xs">
            {dupGroups.length === 0 ? (
              <p className="text-neutral-500">
                在已检查的 {dupScope.checked} 个文件中未发现重复
                {dupScope.skipped > 0 &&
                  `（跳过 ${dupScope.skipped} 个无法读取或超过大小上限的文件）`}
                。
              </p>
            ) : (
              <>
                <p className="text-neutral-700">
                  发现 {dupGroups.length} 组重复文件（共{" "}
                  {dupGroups.reduce((n, g) => n + g.length, 0)} 个，内容完全相同）——
                  已检查 {dupScope.checked} 个
                  {dupScope.skipped > 0 && `，跳过 ${dupScope.skipped} 个`}：
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
    </div>
  );
}
