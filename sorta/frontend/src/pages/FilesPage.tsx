import { useEffect, useState } from "react";
import { useWorkspace } from "../App";
import { api, formatEpoch, formatSize, type FileRow } from "../services/api";

/** 文件库：表格式索引 + 行内浮现操作 + 详情/预览/总结（隐私门）+ 简单重复检测。 */

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

  useEffect(() => {
    setSelected(null);
    setPreview(null);
    setSummary("");
    setDupGroups(null);
    setDupScope(null);
  }, [workspace.scanId]);

  if (!workspace.scanId) {
    return (
      <div className="px-8 py-10">
        <div className="eyebrow">文件库</div>
        <h1 className="mt-3 text-[32px] font-extrabold tracking-tight" style={{ fontWeight: 850 }}>
          尚未建立索引
        </h1>
        <p className="mt-3 max-w-md text-[13px] leading-relaxed text-graphite">
          先在「设置」中选择工作目录并扫描；或在「对话」页直接发起整理，SORTA
          会自动扫描当前授权目录。这里只显示你授权目录的扫描结果，不做后台全盘索引。
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
          (info.has_more ? "（还有更多未加载）" : "") +
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

  async function doPreview(file: FileRow) {
    if (!workspace.scanId) return;
    setBusy("preview");
    setMessage("");
    setSelected(file.path);
    try {
      setPreview(await api.previewFile(workspace.scanId, file.file_id));
      setSummary("");
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function doSummarize(file: FileRow, confirm = false) {
    if (!workspace.scanId) return;
    setBusy("summarize");
    setMessage("");
    setSelected(file.path);
    try {
      const data = await api.summarizeFile(workspace.scanId, file.file_id, confirm);
      setSummary(data.summary);
      setPreview(null);
    } catch (err) {
      const apiErr = err as Error & { code?: string };
      if (apiErr.code === "CONSENT_REQUIRED" && !confirm) {
        const confirmed = window.confirm(`${apiErr.message}\n\n是否允许本次读取？`);
        if (confirmed) {
          setBusy("");
          await doSummarize(file, true);
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
    <div className="px-8 py-8">
      {/* 页头：大计数器 */}
      <div className="flex items-end justify-between">
        <div>
          <div className="eyebrow">文件库 / 当前授权目录</div>
          <div className="mt-2 flex items-baseline gap-3">
            <span className="font-mono text-[44px] leading-none font-semibold tracking-tight tabular-nums">
              {workspace.fileCount}
            </span>
            <span className="text-[13px] text-graphite">个文件已加载</span>
            {workspace.hasMore && (
              <span className="border-b border-caution font-mono text-[10px] text-caution">
                还有更多未加载
              </span>
            )}
          </div>
          <div className="mt-1 font-mono text-[10px] text-graphite">{workspace.root}</div>
        </div>
        <button
          className="border border-rule px-3 py-2 font-mono text-[11px] transition-colors duration-100 hover:border-ink hover:bg-ink hover:text-paper disabled:opacity-40"
          disabled={busy !== ""}
          onClick={rescan}
        >
          {busy === "rescan" ? "扫描中…" : "↻ 重新扫描"}
        </button>
      </div>
      {workspace.skippedDirectories.length > 0 && (
        <div className="mt-3 border-l-2 border-caution pl-3 font-mono text-[11px] text-caution">
          跳过目录（权限不足或规则排除）：{workspace.skippedDirectories.join("、")}
        </div>
      )}

      {/* 搜索：底线下划输入 */}
      <div className="mt-6">
        <input
          className="w-full max-w-lg border-b border-rule-strong bg-transparent px-1 py-2 font-mono text-[13px] transition-colors duration-100 focus:border-signal focus:outline-none"
          placeholder="搜索文件名…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {/* 索引表：行内操作 hover 浮现 */}
      <div className="mt-4 border-t border-ink">
        {files.map((f) => (
          <div
            key={f.file_id}
            className={`file-row group flex items-center gap-4 border-b border-rule py-2 pr-2 transition-colors duration-100 ${
              selected === f.path ? "bg-ink text-paper" : "hover:bg-ink/5"
            }`}
          >
            <button
              className="flex min-w-0 flex-1 items-center gap-4 text-left"
              onClick={() => {
                setSelected(f.path);
                setPreview(null);
                setSummary("");
              }}
            >
              <span className="truncate font-mono text-[12px]">{f.path}</span>
            </button>
            <span
              className={`shrink-0 font-mono text-[10px] ${
                selected === f.path ? "text-paper/60" : "text-graphite"
              } w-16 text-right`}
            >
              {formatSize(f.size)}
            </span>
            <span
              className={`hidden shrink-0 font-mono text-[10px] sm:block ${
                selected === f.path ? "text-paper/60" : "text-graphite"
              } w-36 text-right`}
            >
              {formatEpoch(f.modified_ns / 1e9)}
            </span>
            <span className="row-actions flex shrink-0 gap-1">
              <button
                className="border border-current/30 px-1.5 py-0.5 font-mono text-[10px]"
                disabled={busy !== ""}
                onClick={() => doPreview(f)}
              >
                预览
              </button>
              <button
                className="border border-current/30 px-1.5 py-0.5 font-mono text-[10px]"
                disabled={busy !== ""}
                onClick={() => doSummarize(f)}
              >
                总结
              </button>
            </span>
          </div>
        ))}
        {files.length === 0 && (
          <div className="border-b border-rule py-6 font-mono text-[11px] text-graphite">
            没有匹配的文件。
          </div>
        )}
      </div>

      {/* 详情区 */}
      {selectedFile && (
        <div className="mt-6 border-t-2 border-ink pt-4 rise-in">
          <div className="eyebrow">文件详情</div>
          <div className="mt-2 break-all font-mono text-[13px] font-medium">
            {selectedFile.path}
          </div>
          <div className="mt-1 flex gap-4 font-mono text-[10px] text-graphite">
            <span>.{selectedFile.extension || "无扩展名"}</span>
            <span>{selectedFile.size} B</span>
            <span>{formatEpoch(selectedFile.modified_ns / 1e9)}</span>
          </div>
          <div className="mt-3 flex gap-2">
            <button
              className="border border-rule px-3 py-1.5 font-mono text-[11px] transition-colors duration-100 hover:border-ink hover:bg-ink hover:text-paper disabled:opacity-40"
              disabled={busy !== ""}
              onClick={() => doPreview(selectedFile)}
            >
              本地预览（不发送云端）
            </button>
            <button
              className="bg-ink px-3 py-1.5 font-mono text-[11px] text-paper transition-colors duration-100 hover:bg-signal disabled:opacity-40"
              disabled={busy !== ""}
              onClick={() => doSummarize(selectedFile)}
            >
              {busy === "summarize" ? "总结中…" : "SORTA 总结"}
            </button>
          </div>
          {preview && (
            <div className="mt-3">
              {preview.status === "ok" ? (
                <pre className="max-h-44 overflow-y-auto border-l-2 border-rule-strong bg-ink/[0.03] p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap">
                  {preview.content}
                </pre>
              ) : (
                <div className="border-l-2 border-caution pl-3 font-mono text-[11px] text-caution">
                  无法提取文本：{preview.error}
                </div>
              )}
            </div>
          )}
          {summary && (
            <div className="mt-3 border-l-2 border-verify pl-3 text-[12px] leading-relaxed whitespace-pre-wrap">
              {summary}
            </div>
          )}
        </div>
      )}

      {/* 重复检测（简单版：列出，不删除） */}
      <div className="mt-8 border-t-2 border-ink pt-4">
        <div className="flex items-baseline justify-between">
          <div className="eyebrow">重复文件</div>
          <button
            className="font-mono text-[11px] text-graphite underline-offset-2 hover:text-ink hover:underline disabled:opacity-40"
            disabled={busy !== ""}
            onClick={doDuplicates}
          >
            {busy === "dup" ? "比对中…" : "按内容哈希比对 →"}
          </button>
        </div>
        {dupGroups !== null && dupScope !== null && (
          <div className="mt-2 space-y-1 font-mono text-[11px] leading-relaxed">
            {dupGroups.length === 0 ? (
              <span className="text-graphite">
                在已检查的 {dupScope.checked} 个文件中未发现重复
                {dupScope.skipped > 0 && `（跳过 ${dupScope.skipped} 个无法读取或超限的文件）`}。
              </span>
            ) : (
              dupGroups.map((group, i) => (
                <div key={i} className="border-l-2 border-caution pl-3">
                  {group.map((p) => (
                    <div key={p}>{p}</div>
                  ))}
                </div>
              ))
            )}
            <p className="pt-1 text-[10px] text-graphite">
              仅列出，不自动删除；处理重复请发起整理任务并说明要求。
            </p>
          </div>
        )}
      </div>

      {message && (
        <div className="mt-4 border-l-2 border-ink pl-3 font-mono text-[11px] leading-relaxed">
          {message}
        </div>
      )}
    </div>
  );
}
