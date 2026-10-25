import { useState } from "react";
import { api, type ExecutionDetail, type PlanDetail } from "../services/api";

/** 签名组件：位移台账（transit ledger）。
 * 每一项操作 = 一行「源 ————→ 目标」：hover 时箭头线转为信号红、
 * 展开依据与置信度；执行时逐行翻转状态并滚动计数器。
 * 低置信度默认排除；存在未保存编辑时锁定确认（UX-02）。 */

interface Props {
  plan: PlanDetail;
  toolEvents: string[];
  onPlanReplaced: (plan: PlanDetail) => void;
  onCanceled: () => void;
  onExecuted: (result: ExecutionDetail) => void;
}

const CONFIDENCE_LABEL: Record<string, { text: string; className: string }> = {
  high: { text: "高", className: "text-ink" },
  medium: { text: "中", className: "text-caution" },
  low: { text: "低", className: "text-signal" },
};

export default function PlanPanel({
  plan,
  toolEvents,
  onPlanReplaced,
  onCanceled,
  onExecuted,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [keep, setKeep] = useState<boolean[]>(plan.operations.map((o) => o.confidence !== "low"));
  const [dests, setDests] = useState<string[]>(plan.operations.map((o) => o.destination));
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [execution, setExecution] = useState<ExecutionDetail | null>(null);

  const executed = execution !== null;
  const done = execution
    ? execution.operations.filter((o) => o.status === "succeeded").length
    : 0;

  const steps = [
    { label: "调查", detail: `${toolEvents.length} 次只读调用`, done: true },
    { label: "计划", detail: `${plan.operations.length} 项操作`, done: true },
    executed
      ? { label: "执行", detail: `${done}/${execution!.operations.length} 完成`, done: done === execution!.operations.length }
      : { label: "待确认", detail: "确认前不改文件", done: false },
  ];

  async function run() {
    setError("");
    try {
      setBusy("approve");
      const approval = await api.approvePlan(plan.plan_id, plan.version, plan.plan_hash);
      setBusy("execute");
      const result = await api.execute(approval.approval_id);
      setExecution(result);
      onExecuted(result);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function saveEdits() {
    setError("");
    try {
      setBusy("edit");
      const updated = await api.editPlan(plan.plan_id, {
        keep,
        destinations: dests,
        expected_version: plan.version,
      });
      onPlanReplaced(updated);
      setEditing(false);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function cancel() {
    try {
      await api.rejectPlan(plan.plan_id);
    } catch {
      /* 已拒绝/不存在时忽略 */
    }
    onCanceled();
  }

  async function undo() {
    if (!execution) return;
    setError("");
    try {
      setBusy("undo");
      const result = await api.undo(execution.execution_id);
      setBusy("");
      window.alert(
        result.status === "undone"
          ? `已全部撤销（${result.operations.length} 项）`
          : "部分撤销：冲突项已保留现场，详见历史页",
      );
    } catch (err) {
      setBusy("");
      setError((err as Error).message);
    }
  }

  const errors = plan.issues.filter((i) => i.level === "error");
  const warnings = plan.issues.filter((i) => i.level === "warning");
  const execByIndex = new Map(execution?.operations.map((o) => [o.index, o]) ?? []);

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* 台账头：状态 + 计数器 */}
      <div className="sticky top-0 z-10 border-b border-rule bg-paper px-6 pt-6 pb-4">
        <div className="flex items-baseline justify-between">
          <div className="eyebrow">执行计划 / 台账</div>
          <div className="font-mono text-[10px] text-graphite">
            v{plan.version} · {plan.plan_hash.slice(0, 8)}
          </div>
        </div>
        <div className="mt-2 flex items-baseline gap-3">
          <div className="font-mono text-[40px] leading-none font-semibold tracking-tight tabular-nums">
            {executed ? done : plan.operations.length}
          </div>
          <div className="text-[12px] text-graphite">
            {executed ? (
              <>已执行 / 共 {execution!.operations.length} 项</>
            ) : (
              <>项操作 · 待确认</>
            )}
          </div>
        </div>
        {/* 阶段线 */}
        <div className="mt-3 flex items-center gap-2 font-mono text-[10px]">
          {steps.map((s, i) => (
            <span key={s.label} className="flex items-center gap-2">
              {i > 0 && <span className="h-px w-3 bg-rule-strong" aria-hidden />}
              <span className={s.done ? "text-ink" : "text-graphite"}>
                {s.done ? "■" : "□"} {s.label}
              </span>
              <span className="text-graphite">{s.detail}</span>
            </span>
          ))}
        </div>
      </div>
      {(busy === "approve" || busy === "execute" || busy === "undo") && (
        <div className="marquee-line" aria-hidden />
      )}

      <div className="px-6 py-4">
        <p className="text-[12px] leading-relaxed text-graphite">{plan.summary}</p>
      </div>

      {/* 台账主体 */}
      <div className="border-t border-rule">
        {plan.operations.map((op, i) => {
          const execOp = execByIndex.get(i);
          const conf = CONFIDENCE_LABEL[op.confidence] ?? CONFIDENCE_LABEL.high;
          const rowDim = editing && !keep[i];
          const rowState = execOp
            ? execOp.status === "succeeded"
              ? "text-verify"
              : "text-signal"
            : "";
          return (
            <div
              key={`${op.source_id}-${i}`}
              className={`ledger-row file-row border-b border-rule px-6 py-3 transition-opacity duration-100 ${
                rowDim ? "opacity-35" : ""
              }`}
              tabIndex={0}
            >
              <div className="flex items-center gap-2">
                {editing && !executed && (
                  <input
                    type="checkbox"
                    className="shrink-0"
                    checked={keep[i]}
                    onChange={(e) => {
                      const next = [...keep];
                      next[i] = e.target.checked;
                      setKeep(next);
                    }}
                  />
                )}
                {!editing && executed && (
                  <span className={`shrink-0 font-mono text-[12px] ${rowState}`}>
                    {execOp?.status === "succeeded" ? "✓" : "✗"}
                  </span>
                )}
                <span
                  className={`max-w-[112px] shrink-0 truncate text-right font-mono text-[11px] ${rowState}`}
                  title={op.source}
                >
                  {op.source}
                </span>
                <span className="ledger-arrow" aria-hidden />
                <span className="max-w-[130px] shrink-0 truncate font-mono text-[11px] font-medium" title={op.destination}>
                  {editing ? "" : op.destination}
                </span>
              </div>
              {editing && !executed && (
                <input
                  className="mt-2 w-full border-b border-rule-strong bg-transparent px-1 py-1 font-mono text-[11px] focus:border-signal focus:outline-none disabled:opacity-30"
                  disabled={!keep[i]}
                  value={dests[i]}
                  onChange={(e) => {
                    const next = [...dests];
                    next[i] = e.target.value;
                    setDests(next);
                  }}
                />
              )}
              {/* 依据抽屉：hover / focus 展开 */}
              <div className="ledger-detail">
                <div className="pt-2 pl-6 text-[11px] leading-relaxed text-graphite">
                  <span className={`font-semibold ${conf.className}`}>
                    [{op.action}/{op.confidence === "high" ? "高" : op.confidence === "medium" ? "中" : "低置信·默认不执行"}]
                  </span>{" "}
                  {op.reason}
                </div>
              </div>
            </div>
          );
        })}
        {plan.operations.length === 0 && (
          <div className="px-6 py-6 font-mono text-[11px] text-graphite">
            计划没有操作——所有文件保持原位。
          </div>
        )}
      </div>

      {/* 编辑模式 */}
      <div className="px-6 py-4">
        {editing ? (
          <div className="space-y-2">
            <p className="font-mono text-[10px] text-graphite">
              勾选保留操作；直接改写目标路径。低置信度默认不勾选。
            </p>
            <div className="flex gap-2">
              <button
                className="flex-1 bg-ink px-3 py-2 text-[12px] font-semibold text-paper transition-colors duration-100 hover:bg-signal disabled:opacity-40"
                disabled={busy !== ""}
                onClick={saveEdits}
              >
                {busy === "edit" ? "保存中…" : "保存编辑（v→自动+1）"}
              </button>
              <button
                className="flex-1 border border-rule px-3 py-2 text-[12px] font-medium transition-colors duration-100 hover:border-ink"
                onClick={() => setEditing(false)}
              >
                放弃编辑
              </button>
            </div>
          </div>
        ) : (
          !executed && (
            <button
              className="font-mono text-[11px] text-graphite underline-offset-2 hover:text-ink hover:underline"
              onClick={() => {
                setKeep(plan.operations.map((o) => o.confidence !== "low"));
                setDests(plan.operations.map((o) => o.destination));
                setEditing(true);
              }}
            >
              编辑台账（剔除操作 / 修改目标）
            </button>
          )
        )}
      </div>

      {/* 校验结果：线条式通知 */}
      {(errors.length > 0 || warnings.length > 0 || plan.issues.length === 0) && (
        <div className="border-t border-rule px-6 py-4 space-y-2">
          {plan.issues.length === 0 && (
            <div className="border-l-2 border-verify pl-3 text-[11px] text-verify">
              校验通过：未发现问题。
            </div>
          )}
          {errors.map((issue, i) => (
            <div key={`e-${i}`} className="border-l-2 border-signal pl-3 text-[11px] leading-relaxed">
              <span className="font-mono font-semibold">{issue.code}</span>{" "}
              {issue.message}
            </div>
          ))}
          {warnings.map((issue, i) => (
            <div key={`w-${i}`} className="border-l-2 border-caution pl-3 text-[11px] leading-relaxed text-caution">
              {issue.message}
            </div>
          ))}
          {plan.uncertain_names.length > 0 && (
            <div className="border-l-2 border-rule-strong pl-3 text-[11px] leading-relaxed text-graphite">
              待确认（不移动）：{plan.uncertain_names.join("、")}
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="border-l-2 border-signal px-6 py-3 text-[11px] leading-relaxed">
          {error}
        </div>
      )}

      {editing && !executed && (
        <div className="border-l-2 border-caution px-6 py-2 text-[11px] text-caution">
          存在未保存的编辑：先保存或放弃，才能确认执行。
        </div>
      )}

      {/* 执行 / 撤销 */}
      <div className="sticky bottom-0 border-t-2 border-ink bg-paper px-6 py-4">
        {execution ? (
          <div className="space-y-2">
            <div className="flex items-baseline justify-between">
              <span
                className={`text-[13px] font-semibold ${
                  execution.status === "completed" ? "text-verify" : "text-signal"
                }`}
              >
                {execution.status === "completed" ? "执行完成" : execution.status}
              </span>
              <span className="font-mono text-[10px] text-graphite">
                {execution.execution_id}
              </span>
            </div>
            {execution.operations
              .filter((o) => o.status !== "succeeded")
              .map((o) => (
                <div key={o.index} className="font-mono text-[10px] leading-relaxed text-signal">
                  ✗ {o.source} — {o.error}
                </div>
              ))}
            <button
              className="w-full border border-ink px-3 py-2.5 text-[13px] font-semibold transition-colors duration-100 hover:bg-ink hover:text-paper disabled:opacity-40"
              disabled={busy !== ""}
              onClick={undo}
            >
              {busy === "undo" ? "撤销中…" : "↩ 撤销该批次"}
            </button>
          </div>
        ) : (
          <div className="flex gap-2">
            <button
              className="flex-1 bg-signal px-3 py-2.5 text-[13px] font-semibold text-paper transition-all duration-150 hover:bg-ink disabled:opacity-30"
              disabled={
                plan.has_errors || plan.operations.length === 0 || busy !== "" || editing
              }
              onClick={run}
            >
              {busy === "approve" || busy === "execute" ? "执行中…" : "确认并执行"}
            </button>
            <button
              className="shrink-0 border border-rule px-4 py-2.5 text-[13px] font-medium transition-colors duration-100 hover:border-ink disabled:opacity-40"
              disabled={busy !== "" || editing}
              onClick={cancel}
            >
              取消
            </button>
          </div>
        )}
        <p className="mt-2 font-mono text-[9px] leading-relaxed text-graphite">
          确认前 0 次文件变更 · 确认后逐项记录、不覆盖、可撤销
        </p>
      </div>

      {toolEvents.length > 0 && (
        <details className="border-t border-rule px-6 py-3">
          <summary className="cursor-pointer font-mono text-[10px] tracking-wider text-graphite uppercase">
            只读工具日志 · {toolEvents.length}
          </summary>
          <div className="mt-2 max-h-36 overflow-y-auto font-mono text-[10px] leading-relaxed text-graphite">
            {toolEvents.map((event, i) => (
              <div key={i}>{event}</div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
