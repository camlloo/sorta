import { useEffect, useState } from "react";
import { useWorkspace } from "../App";
import {
  api,
  type PlanDetail,
  type TaskbookAnalysis,
  type TaskbookItem,
} from "../services/api";

/** EXT-03 Project Pack：任务书 → 交付清单 → 候选确认 → 复制计划 → 审批执行。 */

const STATUS_STYLE: Record<TaskbookItem["status"], { label: string; className: string }> = {
  FOUND: { label: "已找到", className: "bg-green-50 text-green-700" },
  MISSING: { label: "缺失", className: "bg-red-50 text-red-700" },
  NEEDS_VERIFICATION: { label: "需人工核验", className: "bg-amber-50 text-amber-700" },
};

export default function PackPage() {
  const workspace = useWorkspace();
  const [taskbookPath, setTaskbookPath] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<TaskbookAnalysis | null>(null);
  const [selections, setSelections] = useState<Record<number, string>>({});
  const [targetDir, setTargetDir] = useState("交付材料");
  const [busy, setBusy] = useState<"" | "analyze" | "plan" | "run">("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [plan, setPlan] = useState<PlanDetail | null>(null);
  const [executed, setExecuted] = useState<{ status: string; id: string } | null>(null);

  useEffect(() => {
    setTaskbookPath(null);
    setAnalysis(null);
    setPlan(null);
    setExecuted(null);
  }, [workspace.scanId]);

  if (!workspace.scanId) {
    return (
      <div className="p-8">
        <h1 className="text-xl font-bold">交付检查（Project Pack）</h1>
        <p className="mt-4 text-sm text-neutral-500">
          尚未扫描工作目录：请先在「设置」中选择包含任务书的项目目录。
        </p>
      </div>
    );
  }

  const textFiles = workspace.files.filter((f) =>
    ["docx", "md", "txt", "pdf"].includes(f.extension),
  );

  async function analyze(confirm = false) {
    if (!taskbookPath || !workspace.scanId) return;
    setBusy("analyze");
    setError("");
    setMessage("");
    setAnalysis(null);
    setPlan(null);
    setExecuted(null);
    try {
      const fileId = workspace.files.find((f) => f.path === taskbookPath)!.file_id;
      const result = await api.analyzeTaskbook(workspace.scanId, fileId, confirm);
      setAnalysis(result);
      const initial: Record<number, string> = {};
      result.items.forEach((item, i) => {
        if (item.candidates.length > 0) initial[i] = item.candidates[0].file_id;
      });
      setSelections(initial);
    } catch (err) {
      const apiErr = err as Error & { code?: string };
      if (apiErr.code === "CONSENT_REQUIRED" && !confirm) {
        const confirmed = window.confirm(`${apiErr.message}\n\n是否允许本次读取？`);
        if (confirmed) {
          setBusy("");
          await analyze(true);
          return;
        }
        setError("未授权读取任务书正文，分析未执行。");
      } else {
        setError(apiErr.message);
      }
    } finally {
      setBusy("");
    }
  }

  async function buildPlan() {
    if (!analysis || !workspace.scanId) return;
    const picked = Object.entries(selections)
      .filter(([, fid]) => fid)
      .map(([idx, fid]) => ({ item: analysis.items[Number(idx)].item, file_id: fid }));
    if (picked.length === 0) {
      setError("请至少为一个交付项选择材料。");
      return;
    }
    setBusy("plan");
    setError("");
    setMessage("");
    setExecuted(null);
    try {
      const created = await api.buildDeliveryPlan(
        workspace.scanId,
        picked,
        targetDir.trim() || "交付材料",
      );
      const detail = await api.getPlan(created.plan_id);
      setPlan(detail);
      if (detail.has_errors) {
        setError("生成的计划存在校验错误（如目标已存在），请调整目标目录后重试。");
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function approveAndRun() {
    if (!plan || !workspace.scanId) return;
    setBusy("run");
    setError("");
    try {
      const approval = await api.approvePlan(plan.plan_id, plan.version, plan.plan_hash);
      const result = await api.execute(approval.approval_id);
      setExecuted({ status: result.status, id: result.execution_id });
      setMessage(
        `交付材料已复制到「${targetDir}/」（${result.operations.filter((o) => o.status === "succeeded").length}/${result.operations.length} 项成功）。原文件保持不动；如需撤销可到「历史」页。`,
      );
      if (workspace.scanId) {
        void api
          .rescanWorkspace(workspace.scanId)
          .then((info) => workspace.setWorkspace(info))
          .catch(() => undefined);
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="h-full overflow-y-auto px-8 py-6">
      <h1 className="text-xl font-bold">交付检查（Project Pack）</h1>
      <p className="mt-1 text-xs text-neutral-500">
        读取任务书提取交付清单，匹配候选材料，确认后复制到交付目录——原件保持不动。
        “找到候选”不等于“内容合格”，需核验项请人工确认。
      </p>

      <div className="mt-4 max-w-3xl space-y-3">
        <div>
          <label className="block text-xs text-neutral-500">任务书文件</label>
          <div className="mt-1 flex gap-2">
            <select
              className="flex-1 rounded border border-neutral-300 px-2 py-2 text-sm"
              value={taskbookPath ?? ""}
              onChange={(e) => setTaskbookPath(e.target.value || null)}
            >
              <option value="">选择任务书（docx / pdf / md / txt）…</option>
              {textFiles.map((f) => (
                <option key={f.file_id} value={f.path}>
                  {f.path}
                </option>
              ))}
            </select>
            <button
              className="rounded bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              disabled={!taskbookPath || busy !== ""}
              onClick={() => analyze(false)}
            >
              {busy === "analyze" ? "分析中…" : "分析交付清单"}
            </button>
          </div>
        </div>

        {analysis && (
          <div className="rounded border border-neutral-200 p-4">
            <div className="flex items-baseline justify-between">
              <h2 className="text-sm font-bold">
                交付清单（{analysis.items.length} 项）
              </h2>
              <div className="flex items-center gap-2">
                <input
                  className="rounded border border-neutral-300 px-2 py-1 text-xs"
                  value={targetDir}
                  onChange={(e) => setTargetDir(e.target.value)}
                  title="交付目录（相对工作目录）"
                />
                <button
                  className="rounded bg-neutral-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                  disabled={busy !== ""}
                  onClick={buildPlan}
                >
                  {busy === "plan" ? "生成中…" : "生成交付计划（复制）"}
                </button>
              </div>
            </div>
            <p className="mt-1 text-xs text-neutral-400">{analysis.note}</p>
            <div className="mt-3 space-y-3">
              {analysis.items.map((item, idx) => {
                const style = STATUS_STYLE[item.status];
                return (
                  <div key={idx} className="rounded border border-neutral-100 p-3">
                    <div className="flex items-center gap-2">
                      <span className={`rounded px-1.5 py-0.5 text-[10px] ${style.className}`}>
                        {style.label}
                      </span>
                      <span className="text-sm font-semibold">{item.item}</span>
                    </div>
                    {item.verification && (
                      <p className="mt-1 text-xs text-neutral-500">
                        核验要求：{item.verification}
                      </p>
                    )}
                    {item.candidates.length > 0 ? (
                      <div className="mt-2 space-y-1">
                        {item.candidates.map((cand) => (
                          <label key={cand.file_id} className="flex items-center gap-2 text-xs">
                            <input
                              type="radio"
                              name={`item-${idx}`}
                              checked={selections[idx] === cand.file_id}
                              onChange={() =>
                                setSelections((s) => ({ ...s, [idx]: cand.file_id }))
                              }
                            />
                            <span className="font-mono">{cand.path}</span>
                            <span className="text-neutral-400">（{cand.reason}）</span>
                          </label>
                        ))}
                        <label className="flex items-center gap-2 text-xs text-neutral-400">
                          <input
                            type="radio"
                            name={`item-${idx}`}
                            checked={!selections[idx]}
                            onChange={() =>
                              setSelections((s) => {
                                const next = { ...s };
                                delete next[idx];
                                return next;
                              })
                            }
                          />
                          不纳入本次交付
                        </label>
                      </div>
                    ) : (
                      <p className="mt-1 text-xs text-red-600">
                        未找到候选材料——该项缺失，或需要你手动补充文件后重新分析。
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {plan && !executed && (
          <div className="rounded border border-neutral-200 p-4">
            <h2 className="text-sm font-bold">
              交付计划（v{plan.version}，{plan.operations.length} 项复制）
            </h2>
            <div className="mt-2 max-h-48 overflow-y-auto text-xs">
              {plan.operations.map((op, i) => (
                <div key={i}>
                  {op.source} → <span className="font-mono">{op.destination}</span>
                </div>
              ))}
            </div>
            {plan.issues.length > 0 && (
              <div className="mt-2 space-y-1">
                {plan.issues.map((issue, i) => (
                  <div
                    key={i}
                    className={`rounded px-2 py-1 text-xs ${
                      issue.level === "error"
                        ? "bg-red-50 text-red-700"
                        : "bg-amber-50 text-amber-700"
                    }`}
                  >
                    {issue.code}: {issue.message}
                  </div>
                ))}
              </div>
            )}
            <button
              className="mt-3 rounded bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              disabled={plan.has_errors || busy !== ""}
              onClick={approveAndRun}
            >
              {busy === "run" ? "复制中…" : "▶ 批准并复制到交付目录"}
            </button>
          </div>
        )}

        {executed && (
          <div
            className={`rounded px-3 py-2 text-xs ${
              executed.status === "completed"
                ? "bg-green-50 text-green-700"
                : "bg-red-50 text-red-700"
            }`}
          >
            执行状态：{executed.status}（{executed.id}）
          </div>
        )}

        {message && (
          <div className="rounded bg-neutral-100 px-3 py-2 text-xs">{message}</div>
        )}
        {error && <div className="rounded bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>}
      </div>
    </div>
  );
}
