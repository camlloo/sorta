import { useCallback, useEffect, useState } from "react";
import {
  api,
  formatEpoch,
  type ExecutionDetail,
  type HistoryEntry,
} from "../services/api";

/** UX-06：独立历史页——不依赖扫描会话；逐项源/目标路径、中文状态、撤销入口。 */

const STATUS_ZH: Record<string, string> = {
  running: "执行中",
  completed: "已完成",
  partial_failed: "部分失败",
  failed: "失败",
};

export default function HistoryPage() {
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ExecutionDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const refresh = useCallback(async () => {
    try {
      const data = await api.history();
      setHistory(data.executions);
      setError("");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function openDetail(executionId: string) {
    setSelected(executionId);
    setDetail(null);
    setMessage("");
    try {
      setDetail(await api.getExecution(executionId));
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function undo() {
    if (!selected) return;
    setBusy(true);
    setMessage("");
    try {
      const result = await api.undo(selected);
      setMessage(
        result.status === "undone"
          ? `已全部撤销（${result.operations.length} 项）。`
          : `部分撤销：${result.operations.filter((o) => o.status === "undone").length}/${result.operations.length} 项成功；冲突项已保留现场，请人工核对。`,
      );
      await refresh();
      await openDetail(selected);
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="h-full overflow-y-auto px-8 py-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">执行历史</h1>
        <button
          className="rounded border border-neutral-300 px-3 py-1.5 text-xs disabled:opacity-50"
          disabled={busy}
          onClick={refresh}
        >
          ↻ 刷新
        </button>
      </div>
      <p className="mt-1 text-xs text-neutral-500">
        独立于当前扫描会话；扫描过期仍可查看历史并对无冲突批次尝试撤销。
      </p>

      {error && (
        <div className="mt-4 max-w-3xl rounded bg-red-50 px-3 py-2 text-xs text-red-700">
          {error}
        </div>
      )}
      {message && (
        <div className="mt-4 max-w-3xl rounded bg-neutral-100 px-3 py-2 text-xs">{message}</div>
      )}

      {loaded && history.length === 0 && (
        <p className="mt-6 text-sm text-neutral-500">暂无执行历史。</p>
      )}

      {history.length > 0 && (
        <div className="mt-4 max-w-3xl overflow-hidden rounded border border-neutral-200">
          <table className="w-full text-left text-sm">
            <thead className="bg-neutral-50 text-xs text-neutral-500">
              <tr>
                <th className="px-3 py-2 font-medium">批次</th>
                <th className="px-3 py-2 font-medium">状态</th>
                <th className="px-3 py-2 font-medium">开始</th>
                <th className="px-3 py-2 font-medium">结束</th>
              </tr>
            </thead>
            <tbody>
              {history.map((e) => (
                <tr
                  key={e.execution_id}
                  className={`cursor-pointer border-t border-neutral-100 hover:bg-neutral-50 ${
                    selected === e.execution_id ? "bg-blue-50" : ""
                  }`}
                  onClick={() => openDetail(e.execution_id)}
                >
                  <td className="px-3 py-1.5 font-mono text-xs">{e.execution_id}</td>
                  <td className="px-3 py-1.5 text-xs">
                    {STATUS_ZH[e.status] ?? e.status}
                    {(e.status === "completed" || e.status === "partial_failed") && (
                      <span className="ml-1 text-neutral-400">（可尝试撤销）</span>
                    )}
                  </td>
                  <td className="px-3 py-1.5 text-xs text-neutral-500">
                    {formatEpoch(e.started_at)}
                  </td>
                  <td className="px-3 py-1.5 text-xs text-neutral-500">
                    {formatEpoch(e.finished_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {detail && (
        <div className="mt-6 max-w-3xl rounded border border-neutral-200 p-4">
          <h2 className="text-sm font-bold">
            批次详情（{STATUS_ZH[detail.status] ?? detail.status}）
          </h2>
          <div className="mt-2 space-y-1 text-xs">
            {detail.operations.map((op) => (
              <div key={op.index}>
                {op.status === "succeeded" ? "✅" : "❌"} [{op.action}]{" "}
                <span className="font-mono">{op.source}</span> →{" "}
                <span className="font-mono">{op.destination}</span>
                {op.error && <span className="text-red-600">（{op.error}）</span>}
              </div>
            ))}
          </div>
          {(detail.status === "completed" || detail.status === "partial_failed") && (
            <>
              <p className="mt-3 text-xs text-neutral-500">
                撤销为满足条件时的尽力恢复：目标未被修改且原路径空闲才会移回；冲突项将保留现场。
              </p>
              <button
                className="mt-2 rounded border border-neutral-300 px-3 py-1.5 text-xs disabled:opacity-50"
                disabled={busy}
                onClick={undo}
              >
                {busy ? "撤销中…" : "撤销该批次"}
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
