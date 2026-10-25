import { useEffect, useState } from "react";
import { useWorkspace } from "../App";
import {
  api,
  formatEpoch,
  formatSize,
  type DuplicateReport,
  type FileRow,
  type PlanDetail,
} from "../services/api";

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
  const [dupReport, setDupReport] = useState<DuplicateReport | null>(null);
  const [archivePick, setArchivePick] = useState<Record<string, boolean>>({});
  const [ocrText, setOcrText] = useState<{ text: string; source: string } | null>(null);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    setSelected(null);
    setPreview(null);
    setSummary("");
    setDupReport(null);
    setArchivePick({});
    setOcrText(null);
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
      setDupReport(data);
      const picks: Record<string, boolean> = {};
      data.groups.forEach((g) =>
        g.files.forEach((f) => {
          if (f !== g.keep) picks[g.file_ids[f]] = true;
        }),
      );
      setArchivePick(picks);
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function archivePicked() {
    if (!workspace.scanId) return;
    const fileIds = Object.entries(archivePick)
      .filter(([, v]) => v)
      .map(([k]) => k);
    if (fileIds.length === 0) {
      setMessage("请先勾选要归档的副本。");
      return;
    }
    setBusy("archive");
    setMessage("");
    try {
      const created = await api.archiveDuplicates(workspace.scanId, fileIds);
      const detail: PlanDetail = await api.getPlan(created.plan_id);
      if (detail.has_errors) {
        const first = detail.issues.find((i) => i.level === "error");
        setMessage(`归档计划存在校验错误：${first?.message ?? ""}`);
        return;
      }
      const approval = await api.approvePlan(created.plan_id, created.version, created.plan_hash);
      const result = await api.execute(approval.approval_id);
      setMessage(
        result.status === "completed"
          ? `已归档 ${result.operations.length} 个副本到「重复文件归档/」（可撤销：历史页 ${result.execution_id}）。`
          : `归档部分失败（${result.status}），详见历史页 ${result.execution_id}。`,
      );
      const info = await api.rescanWorkspace(workspace.scanId);
      workspace.setWorkspace(info);
      const data = await api.findDuplicates(info.scan_id);
      setDupReport(data);
      setArchivePick({});
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function doOcr(confirm = false) {
    if (!selectedFile || !workspace.scanId) return;
    setBusy("ocr");
    setMessage("");
    setOcrText(null);
    try {
      const result = await api.ocrFile(workspace.scanId, selectedFile.file_id, confirm);
      setOcrText({ text: result.text, source: result.source });
    } catch (err) {
      const apiErr = err as Error & { code?: string };
      if (apiErr.code === "CONSENT_REQUIRED" && !confirm) {
        const confirmed = window.confirm(`${apiErr.message}\n\n是否允许本次识别？`);
        if (confirmed) {
          setBusy("");
          await doOcr(true);
          return;
        }
        setMessage("未授权发送图片，OCR 未执行。");
      } else {
        setMessage(apiErr.message);
      }
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
          {["png", "jpg", "jpeg"].includes(selectedFile.extension) && (
            <button
              className="rounded border border-neutral-300 px-3 py-1.5 text-xs disabled:opacity-50"
              disabled={busy !== ""}
              onClick={() => doOcr(false)}
            >
              {busy === "ocr" ? "识别中…" : "🔍 OCR 识别（发送云端视觉模型）"}
            </button>
          )}
          {ocrText && (
            <div className="mt-3">
              <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap rounded bg-neutral-50 p-3 text-xs">
                {ocrText.text}
              </pre>
              <p className="mt-1 text-xs text-amber-600">来源：{ocrText.source}</p>
            </div>
          )}
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
        <h2 className="text-sm font-bold">重复文件治理</h2>
        <p className="mt-1 text-xs text-neutral-500">
          先按大小分组、仅对同大小文件计算内容哈希；只列出与建议，不自动删除。
        </p>
        <button
          className="mt-2 rounded border border-neutral-300 px-3 py-1.5 text-xs disabled:opacity-50"
          disabled={busy !== ""}
          onClick={doDuplicates}
        >
          {busy === "dup" ? "比对中…" : "✨ 查找重复文件"}
        </button>
        {dupReport !== null && (
          <div className="mt-3 space-y-2 text-xs">
            <p className="text-neutral-700">
              {dupReport.note}
              {dupReport.total_wasted_bytes > 0 &&
                ` 可释放空间约 ${formatSize(dupReport.total_wasted_bytes)}（按副本数×文件大小估算）。`}
            </p>
            {dupReport.groups.map((group, gi) => (
              <div key={gi} className="rounded border border-neutral-100 p-2">
                <div className="font-semibold">
                  组 {gi + 1}：{group.files.length} 个副本 · {formatSize(group.size)} · 浪费{" "}
                  {formatSize(group.wasted_bytes)}
                </div>
                <div className="mt-1 text-neutral-400">建议保留：{group.keep}</div>
                {group.files.map((f) => (
                  <label key={f} className="mt-1 flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={archivePick[group.file_ids[f]] ?? false}
                      onChange={(e) =>
                        setArchivePick((p) => ({
                          ...p,
                          [group.file_ids[f]]: e.target.checked,
                        }))
                      }
                    />
                    <span className={`font-mono ${f === group.keep ? "text-neutral-400" : ""}`}>
                      {f}
                      {f === group.keep && "（建议保留）"}
                    </span>
                  </label>
                ))}
              </div>
            ))}
            {dupReport.groups.length > 0 && (
              <button
                className="rounded bg-neutral-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                disabled={busy !== ""}
                onClick={archivePicked}
              >
                {busy === "archive" ? "归档中…" : "归档所选副本（生成计划并执行，可撤销）"}
              </button>
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
