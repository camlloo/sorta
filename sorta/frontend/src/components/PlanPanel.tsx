import { useState } from "react";
import { api, type ExecutionDetail, type PlanDetail } from "../services/api";

/** 示意图 ⑥⑦：右侧执行计划面板（时间线 + 预览 + 编辑 + 确认/取消 + 撤销）。 */

interface Props {
  plan: PlanDetail;
  toolEvents: string[];
  onPlanReplaced: (plan: PlanDetail) => void;
  onCanceled: () => void;
  onExecuted: (result: ExecutionDetail) => void;
}

export default function PlanPanel({
  plan,
  toolEvents,
  onPlanReplaced,
  onCanceled,
  onExecuted,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [keep, setKeep] = useState<boolean[]>(plan.operations.map(() => true));
  const [dests, setDests] = useState<string[]>(plan.operations.map((o) => o.destination));
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [execution, setExecution] = useState<ExecutionDetail | null>(null);

  const executed = execution !== null;
  const steps = [
    { title: "查找文件", detail: `只读调查完成，${toolEvents.length} 次工具调用` },
    { title: "分类整理", detail: `生成 ${plan.operations.length} 项移动/复制操作` },
    executed
      ? { title: "执行完成", detail: "计划已按批准内容执行，可在下方撤销" }
      : { title: "等待确认", detail: "确认无误后执行，可先在预览中编辑" },
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
          : "部分撤销：冲突项已保留现场，详见文件库执行历史",
      );
    } catch (err) {
      setBusy("");
      setError((err as Error).message);
    }
  }

  const errors = plan.issues.filter((i) => i.level === "error");
  const warnings = plan.issues.filter((i) => i.level === "warning");

  return (
    <div className="flex h-full flex-col">
      <h2 className="text-lg font-bold">执行计划</h2>
      <div className="mt-4 space-y-3">
        {steps.map((step, i) => (
          <div key={step.title} className="flex gap-3">
            <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-neutral-900 text-xs font-bold text-white">
              {i + 1}
            </div>
            <div>
              <div className="text-sm font-semibold">{step.title}</div>
              <div className="text-xs text-neutral-500">{step.detail}</div>
            </div>
          </div>
        ))}
      </div>

      <hr className="my-4 border-neutral-200" />
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-semibold">预览</h3>
        <span className="text-xs text-neutral-500">
          v{plan.version} · {plan.operations.length} 项操作
        </span>
      </div>
      <div className="mt-2 max-h-64 overflow-y-auto rounded border border-neutral-200">
        <table className="w-full text-left text-xs">
          <tbody>
            {plan.operations.map((op, i) => (
              <tr
                key={`${op.source_id}-${i}`}
                className="border-b border-neutral-100 last:border-0"
              >
                <td className="px-2 py-1.5 align-top">
                  <input
                    type="checkbox"
                    checked={editing ? keep[i] : true}
                    disabled={!editing}
                    onChange={(e) => {
                      const next = [...keep];
                      next[i] = e.target.checked;
                      setKeep(next);
                    }}
                  />
                </td>
                <td className="px-2 py-1.5">
                  <div className="font-mono">{op.destination}</div>
                  <div className="mt-0.5 text-neutral-400">{op.reason}</div>
                  <div className="mt-0.5 text-neutral-400">
                    [{op.action}/{op.confidence}]
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editing ? (
        <div className="mt-2 space-y-1">
          {plan.operations.map((op, i) => (
            <input
              key={`${op.source_id}-input-${i}`}
              className="w-full rounded border border-neutral-300 px-2 py-1 text-xs"
              value={dests[i]}
              onChange={(e) => {
                const next = [...dests];
                next[i] = e.target.value;
                setDests(next);
              }}
            />
          ))}
          <div className="flex gap-2">
            <button
              className="flex-1 rounded bg-neutral-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
              disabled={busy !== ""}
              onClick={saveEdits}
            >
              {busy === "edit" ? "保存中…" : "保存编辑（自动重新校验）"}
            </button>
            <button
              className="flex-1 rounded border border-neutral-300 px-3 py-1.5 text-xs"
              onClick={() => setEditing(false)}
            >
              放弃编辑
            </button>
          </div>
        </div>
      ) : (
        !executed && (
          <button
            className="mt-2 text-left text-xs text-neutral-500 hover:text-neutral-800"
            onClick={() => {
              setKeep(plan.operations.map(() => true));
              setDests(plan.operations.map((o) => o.destination));
              setEditing(true);
            }}
          >
            编辑计划（勾选剔除 / 修改目标路径）
          </button>
        )
      )}

      {errors.map((issue, i) => (
        <div
          key={`e-${i}`}
          className="mt-2 rounded bg-red-50 px-3 py-2 text-xs text-red-700"
        >
          {issue.operation_index !== null && `操作#${issue.operation_index} `}
          {issue.code}: {issue.message}
        </div>
      ))}
      {warnings.map((issue, i) => (
        <div
          key={`w-${i}`}
          className="mt-2 rounded bg-amber-50 px-3 py-2 text-xs text-amber-700"
        >
          {issue.operation_index !== null && `操作#${issue.operation_index} `}
          {issue.code}: {issue.message}
        </div>
      ))}
      {plan.issues.length === 0 && (
        <div className="mt-2 rounded bg-green-50 px-3 py-2 text-xs text-green-700">
          校验通过：未发现问题。
        </div>
      )}
      {plan.uncertain_names.length > 0 && (
        <div className="mt-2 rounded bg-blue-50 px-3 py-2 text-xs text-blue-700">
          待确认文件（不移动）：{plan.uncertain_names.join("、")}
        </div>
      )}

      {error && (
        <div className="mt-2 rounded bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>
      )}

      {execution ? (
        <div className="mt-4 space-y-2">
          <div
            className={`rounded px-3 py-2 text-xs ${
              execution.status === "completed"
                ? "bg-green-50 text-green-700"
                : "bg-red-50 text-red-700"
            }`}
          >
            执行状态：{execution.status}（{execution.execution_id}）
          </div>
          {execution.operations.map((op) => (
            <div key={op.index} className="text-xs">
              {op.status === "succeeded" ? "✅" : "❌"} {op.source} → {op.destination}
              {op.error && <span className="text-red-600">（{op.error}）</span>}
            </div>
          ))}
          <button
            className="w-full rounded border border-neutral-300 px-3 py-2 text-sm disabled:opacity-50"
            disabled={busy !== ""}
            onClick={undo}
          >
            {busy === "undo" ? "撤销中…" : "撤销该批次"}
          </button>
        </div>
      ) : (
        <div className="mt-auto grid grid-cols-2 gap-2 pt-4">
          <button
            className="rounded bg-neutral-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
            disabled={plan.has_errors || plan.operations.length === 0 || busy !== ""}
            onClick={run}
          >
            {busy === "approve" || busy === "execute" ? "执行中…" : "▶ 确认并执行"}
          </button>
          <button
            className="rounded border border-neutral-300 px-3 py-2 text-sm disabled:opacity-40"
            disabled={busy !== ""}
            onClick={cancel}
          >
            ↩ 取消
          </button>
        </div>
      )}

      {toolEvents.length > 0 && (
        <details className="mt-4">
          <summary className="cursor-pointer text-xs text-neutral-500">
            只读工具调用日志（{toolEvents.length} 次）
          </summary>
          <div className="mt-1 max-h-40 overflow-y-auto text-xs text-neutral-500">
            {toolEvents.map((event, i) => (
              <div key={i}>{event}</div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
