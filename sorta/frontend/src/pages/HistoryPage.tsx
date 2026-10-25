import { useCallback, useEffect, useState } from "react";
import {
  api,
  formatEpoch,
  type ExecutionDetail,
  type HistoryEntry,
} from "../services/api";

/** 历史页：独立于扫描会话的执行台账。中文状态 + 逐项位移 + 撤销（UX-06）。 */

const STATUS: Record<string, { label: string; className: string }> = {
  running: { label: "执行中", className: "text-caution" },
  completed: { label: "已完成", className: "text-verify" },
  partial_failed: { label: "部分失败", className: "text-signal" },
  failed: { label: "失败", className: "text-signal" },
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
    <div className="px-8 py-8">
      <div className="flex items-end justify-between">
        <div>
          <div className="eyebrow">执行历史 / 独立台账</div>
          <div className="mt-2 flex items-baseline gap-3">
            <span className="font-mono text-[44px] leading-none font-semibold tracking-tight tabular-nums">
              {history.length}
            </span>
            <span className="text-[13px] text-graphite">个执行批次</span>
          </div>
          <p className="mt-1 font-mono text-[10px] text-graphite">
            扫描过期仍可查看与撤销
          </p>
        </div>
        <button
          className="border border-rule px-3 py-2 font-mono text-[11px] transition-colors duration-100 hover:border-ink hover:bg-ink hover:text-paper disabled:opacity-40"
          disabled={busy}
          onClick={refresh}
        >
          ↻ 刷新
        </button>
      </div>

      {error && (
        <div className="mt-4 border-l-2 border-signal pl-3 font-mono text-[11px] text-signal">
          {error}
        </div>
      )}
      {message && (
        <div className="mt-4 border-l-2 border-ink pl-3 font-mono text-[11px] leading-relaxed">
          {message}
        </div>
      )}

      {loaded && history.length === 0 && (
        <p className="mt-8 text-[13px] text-graphite">暂无执行历史。</p>
      )}

      {history.length > 0 && (
        <div className="mt-6 border-t border-ink">
          {history.map((e) => {
            const st = STATUS[e.status] ?? { label: e.status, className: "text-graphite" };
            const active = selected === e.execution_id;
            return (
              <button
                key={e.execution_id}
                className={`flex w-full items-baseline gap-4 border-b border-rule px-2 py-3 text-left transition-colors duration-100 ${
                  active ? "bg-ink text-paper" : "hover:bg-ink/5"
                }`}
                onClick={() => openDetail(e.execution_id)}
              >
                <span className="shrink-0 font-mono text-[10px] opacity-60">
                  {formatEpoch(e.started_at)}
                </span>
                <span
                  className={`min-w-0 flex-1 truncate font-mono text-[11px] ${
                    active ? "" : "text-graphite"
                  }`}
                >
                  {e.execution_id}
                </span>
                <span className={`shrink-0 text-[12px] font-semibold ${active ? "" : st.className}`}>
                  {st.label}
                  {(e.status === "completed" || e.status === "partial_failed") && (
                    <span className={active ? " opacity-60" : " text-graphite"}> · 可撤销</span>
                  )}
                </span>
              </button>
            );
          })}
        </div>
      )}

      {detail && (
        <div className="mt-6 border-t-2 border-ink pt-4 rise-in">
          <div className="flex items-baseline justify-between">
            <div className="eyebrow">批次台账</div>
            <span className="font-mono text-[10px] text-graphite">
              {detail.execution_id}
            </span>
          </div>
          <div className="mt-3 border-t border-rule">
            {detail.operations.map((op) => (
              <div key={op.index} className="flex items-center gap-2 border-b border-rule py-2">
                <span
                  className={`shrink-0 font-mono text-[11px] ${
                    op.status === "succeeded" ? "text-verify" : "text-signal"
                  }`}
                >
                  {op.status === "succeeded" ? "✓" : "✗"}
                </span>
                <span className="max-w-[140px] shrink-0 truncate text-right font-mono text-[11px]">
                  {op.source}
                </span>
                <span className="h-px min-w-3 flex-1 bg-rule-strong" aria-hidden />
                <span className="max-w-[150px] shrink-0 truncate font-mono text-[11px]">
                  {op.destination}
                </span>
                {op.error && (
                  <span className="shrink-0 font-mono text-[10px] text-signal">{op.error}</span>
                )}
              </div>
            ))}
          </div>
          {(detail.status === "completed" || detail.status === "partial_failed") && (
            <>
              <p className="mt-3 font-mono text-[10px] text-graphite">
                尽力恢复：目标未改且原路径空闲才移回
              </p>
              <button
                className="mt-2 border border-ink px-4 py-2 text-[13px] font-semibold transition-colors duration-100 hover:bg-ink hover:text-paper disabled:opacity-40"
                disabled={busy}
                onClick={undo}
              >
                {busy ? "撤销中…" : "↩ 撤销该批次"}
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
