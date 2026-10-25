import { useCallback, useEffect, useRef, useState } from "react";
import { useWorkspace } from "../App";
import PlanPanel from "../components/PlanPanel";
import {
  api,
  CAPABILITIES,
  type ExecutionDetail,
  type JobSnapshot,
  type PlanDetail,
  type TaskResult,
  type Template,
} from "../services/api";

interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

/** UX-01/05 + EXT-02：对话状态持久化；任务分流（查找/问答/整理/澄清）；
 * 待确认计划时输入即修改计划；模板选择器注入整理偏好。 */

const KEY = {
  messages: "sorta_chat_messages",
  planId: "sorta_plan_id",
  toolEvents: "sorta_tool_events",
  jobId: "sorta_job_id",
};

function load<T>(key: string, fallback: T): T {
  const raw = sessionStorage.getItem(key);
  if (!raw) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function diffPlans(oldOps: PlanDetail["operations"], newOps: PlanDetail["operations"]) {
  const oldBySource = new Map(oldOps.map((o) => [o.source_id, o]));
  let added = 0;
  let removed = 0;
  let changed = 0;
  for (const op of newOps) {
    const prev = oldBySource.get(op.source_id);
    if (!prev) added += 1;
    else if (prev.destination !== op.destination || prev.action !== op.action) changed += 1;
  }
  for (const op of oldOps) {
    if (!newOps.some((n) => n.source_id === op.source_id)) removed += 1;
  }
  return { added, removed, changed };
}

function findMatchesText(result: Extract<TaskResult, { task_type: "find" }>): string {
  const lines =
    result.matches.length === 0
      ? ["没有匹配的文件。"]
      : result.matches.map((m) => `📄 ${m.path}（${m.reason}）`);
  return `找到 ${result.matches.length} 个匹配文件：\n${lines.join("\n")}\n\n${result.note}`;
}

function taskResultMessage(result: TaskResult, toolEvents: string[]): string {
  if (result.task_type === "organize") {
    return ""; // 计划走右侧面板；此占位不会使用
  }
  if (result.task_type === "find") {
    return `${findMatchesText(result)}\n\n共 ${toolEvents.length} 次只读工具调用。`;
  }
  if (result.task_type === "ask") {
    const refs = result.references.length
      ? `\n\n涉及文件：${result.references.join("、")}`
      : "";
    return `${result.answer}${refs}`;
  }
  return result.question;
}

export default function ChatPage() {
  const workspace = useWorkspace();
  const [messages, setMessages] = useState<ChatMessage[]>(() => load(KEY.messages, []));
  const [input, setInput] = useState("");
  const [plan, setPlan] = useState<PlanDetail | null>(null);
  const [toolEvents, setToolEvents] = useState<string[]>(() => load(KEY.toolEvents, []));
  const [planning, setPlanning] = useState<boolean>(() => !!sessionStorage.getItem(KEY.jobId));
  const [jobId, setJobId] = useState<string | null>(() => sessionStorage.getItem(KEY.jobId));
  const [lastExecution, setLastExecution] = useState<ExecutionDetail | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templateId, setTemplateId] = useState<number | null>(null);
  const planPanelRef = useRef<HTMLDivElement>(null);

  const planningBlocked = planning || !workspace.scanId || !workspace.hasApiKey;
  const disabled = planningBlocked || input.trim() === "";
  const canRevise = plan !== null && !lastExecution && !planning;

  // UX-05：状态持久化
  useEffect(() => {
    sessionStorage.setItem(KEY.messages, JSON.stringify(messages));
    sessionStorage.setItem(KEY.toolEvents, JSON.stringify(toolEvents));
  }, [messages, toolEvents]);

  useEffect(() => {
    if (workspace.hasApiKey && templates.length === 0) {
      api
        .listTemplates()
        .then((data) => setTemplates(data.templates.filter((t) => t.enabled)))
        .catch(() => undefined);
    }
  }, [workspace.hasApiKey, templates.length]);

  // 恢复：计划详情 + 未完成任务轮询
  useEffect(() => {
    const savedPlanId = load<number | null>(KEY.planId, null);
    if (savedPlanId && !plan) {
      api
        .getPlan(savedPlanId)
        .then(setPlan)
        .catch(() => sessionStorage.removeItem(KEY.planId));
    }
    const savedJobId = sessionStorage.getItem(KEY.jobId);
    if (savedJobId && planning) {
      void pollJob(savedJobId).catch((err: Error & { status?: number }) => {
        setMessages((m) => [
          ...m,
          {
            role: "assistant",
            content:
              err.status === 404
                ? "任务已中断（服务可能已重启）。请重新发起请求。"
                : `任务状态查询失败：${err.message}`,
          },
        ]);
        setPlanning(false);
        setJobId(null);
        sessionStorage.removeItem(KEY.jobId);
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (plan) sessionStorage.setItem(KEY.planId, JSON.stringify(plan.plan_id));
    else sessionStorage.removeItem(KEY.planId);
  }, [plan]);

  const pollJob = useCallback(
    async (id: string): Promise<JobSnapshot> => {
      for (;;) {
        await new Promise((r) => setTimeout(r, 1500));
        let snapshot: JobSnapshot;
        try {
          snapshot = await api.getJob(id);
        } catch (err) {
          if ((err as { status?: number }).status === 404) {
            const interrupted = new Error("任务已中断（服务可能已重启），请重新发起请求");
            (interrupted as { status?: number }).status = 404;
            throw interrupted;
          }
          throw err;
        }
        setToolEvents(snapshot.tool_events);
        if (
          snapshot.status === "completed" ||
          snapshot.status === "failed" ||
          snapshot.status === "canceled"
        ) {
          return snapshot;
        }
      }
    },
    [],
  );

  const finishTask = useCallback(
    (snapshot: JobSnapshot, mode: "new" | "revise", prevPlan: PlanDetail | null) => {
      if (snapshot.status === "completed" && snapshot.result) {
        const result = snapshot.result;
        if (result.task_type === "organize") {
          void api
            .getPlan(result.plan_id)
            .then((detail) => {
              setPlan(detail);
              if (mode === "new") {
                setMessages((m) => [
                  ...m,
                  {
                    role: "assistant",
                    content:
                      `${detail.summary}\n\n共 ${snapshot.tool_events.length} 次只读工具调用，` +
                      `生成 ${detail.operations.length} 项操作；右侧可预览、编辑并确认执行。` +
                      (detail.uncertain_names.length
                        ? `另有 ${detail.uncertain_names.length} 个文件证据不足，已列入待确认。`
                        : ""),
                  },
                ]);
              } else if (prevPlan) {
                const d = diffPlans(prevPlan.operations, detail.operations);
                setMessages((m) => [
                  ...m,
                  {
                    role: "assistant",
                    content:
                      `已按你的要求更新计划（v${detail.version}）：新增 ${d.added} 项、` +
                      `移除 ${d.removed} 项、调整 ${d.changed} 项目标。` +
                      `原审批已失效，请在右侧重新确认。`,
                  },
                ]);
              }
              sessionStorage.setItem(KEY.planId, JSON.stringify(result.plan_id));
              void workspace.refreshFiles(result.scan_id);
            })
            .catch((err: Error) => {
              setMessages((m) => [
                ...m,
                { role: "assistant", content: `获取计划失败：${err.message}` },
              ]);
            });
        } else {
          // 查找 / 问答 / 澄清：结果只在对话流中，不产生计划面板
          setPlan(null);
          sessionStorage.removeItem(KEY.planId);
          setMessages((m) => [
            ...m,
            { role: "assistant", content: taskResultMessage(result, snapshot.tool_events) },
          ]);
        }
      } else if (snapshot.status === "failed") {
        setMessages((m) => [
          ...m,
          { role: "assistant", content: `处理失败：${snapshot.error?.message ?? "未知错误"}` },
        ]);
      } else {
        setMessages((m) => [...m, { role: "assistant", content: "已取消本次任务。" }]);
      }
      setPlanning(false);
      setJobId(null);
      sessionStorage.removeItem(KEY.jobId);
    },
    [workspace],
  );

  const send = useCallback(
    async (text: string, taskType: "auto" | "find" | "ask" | "organize" = "auto") => {
      const clean = text.trim();
      if (!clean || planningBlocked || !workspace.scanId) return;
      setMessages((m) => [...m, { role: "user", content: clean }]);
      setInput("");
      setPlanning(true);
      setToolEvents([]);
      setLastExecution(null);

      const reviseMode = plan !== null && !lastExecution;
      const prevPlan = plan;
      if (!reviseMode) {
        setPlan(null);
        sessionStorage.removeItem(KEY.planId);
      }

      try {
        const { job_id } = reviseMode
          ? await api.revisePlan(plan!.plan_id, clean, plan!.version)
          : await api.createTask(
              workspace.scanId,
              clean,
              taskType,
              templateId,
              workspace.privacy,
            );
        setJobId(job_id);
        sessionStorage.setItem(KEY.jobId, job_id);
        const snapshot = await pollJob(job_id);
        finishTask(snapshot, reviseMode ? "revise" : "new", reviseMode ? prevPlan : null);
      } catch (err) {
        setMessages((m) => [
          ...m,
          { role: "assistant", content: `处理失败：${(err as Error).message}` },
        ]);
        setPlanning(false);
        setJobId(null);
        sessionStorage.removeItem(KEY.jobId);
      }
    },
    [planningBlocked, workspace, plan, lastExecution, templateId, pollJob, finishTask],
  );

  const cancelPlanning = useCallback(async () => {
    if (jobId) await api.cancelJob(jobId).catch(() => undefined);
  }, [jobId]);

  const startNewTask = useCallback(() => {
    setPlan(null);
    setToolEvents([]);
    setLastExecution(null);
    setMessages((m) => [
      ...m,
      { role: "system", content: "—— 已开始新任务（原计划保留在数据库中，可在历史中查看）——" },
    ]);
  }, []);

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center justify-between border-b border-neutral-200 px-8 py-3 text-sm">
          <div>
            <span className="font-semibold">SORTA</span>
            <span className="ml-2 text-neutral-400">· {workspace.model || "未配置模型"}</span>
            {!workspace.hasApiKey && <span className="ml-2 text-red-600">· 未配置 API Key</span>}
          </div>
          {canRevise && (
            <button
              className="rounded border border-neutral-300 px-2 py-1 text-xs text-neutral-600 hover:bg-neutral-50"
              onClick={startNewTask}
            >
              新建任务
            </button>
          )}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-8 py-6">
          {messages.length === 0 ? (
            <div className="mx-auto max-w-xl">
              <h1 className="mt-16 text-center text-4xl font-bold leading-snug">
                你好，
                <br />
                我是 SORTA
              </h1>
              <p className="mt-4 text-center text-neutral-500">
                用自然语言，帮你查找、理解、整理和管理文件。
              </p>
              <div className="mt-12 grid grid-cols-2 gap-3">
                {CAPABILITIES.map((card) => (
                  <button
                    key={card.title}
                    className="rounded-lg border border-neutral-200 p-4 text-left transition-colors hover:border-neutral-400 disabled:opacity-40"
                    disabled={planningBlocked}
                    onClick={() => send(card.example, card.taskType)}
                  >
                    <div className="text-sm font-semibold">
                      <span className="mr-1.5">{card.icon}</span>
                      {card.title}
                    </div>
                    <div className="mt-1 text-xs text-neutral-500">“{card.example}”</div>
                  </button>
                ))}
              </div>
              {!workspace.scanId && (
                <p className="mt-6 text-center text-xs text-neutral-400">
                  请先在「设置」中选择工作目录
                </p>
              )}
            </div>
          ) : (
            <div className="mx-auto max-w-2xl space-y-4">
              {messages.map((msg, i) =>
                msg.role === "system" ? (
                  <div key={i} className="text-center text-xs text-neutral-400">
                    {msg.content}
                  </div>
                ) : (
                  <div key={i} className={msg.role === "user" ? "text-right" : ""}>
                    <div
                      className={`inline-block max-w-[85%] whitespace-pre-wrap rounded-lg px-4 py-2.5 text-sm ${
                        msg.role === "user"
                          ? "bg-neutral-900 text-white"
                          : "border border-neutral-200 bg-white"
                      }`}
                    >
                      {msg.content}
                    </div>
                  </div>
                ),
              )}
              {planning && (
                <div className="text-sm text-neutral-500">
                  <span className="animate-pulse">
                    {canRevise || plan ? "SORTA 正在更新计划…" : "SORTA 正在处理…"}
                  </span>
                  {toolEvents.length > 0 && (
                    <div className="mt-1 max-h-32 overflow-y-auto text-xs text-neutral-400">
                      {toolEvents.map((event, i) => (
                        <div key={i}>{event}</div>
                      ))}
                    </div>
                  )}
                  <button
                    className="mt-1 text-xs text-red-600 hover:underline"
                    onClick={cancelPlanning}
                  >
                    取消（在安全停点生效）
                  </button>
                </div>
              )}
              {lastExecution && (
                <div className="text-xs text-neutral-500">
                  最近执行：{lastExecution.status}（{lastExecution.execution_id}）
                </div>
              )}
            </div>
          )}
        </div>
        <div className="border-t border-neutral-200 px-8 py-4">
          <div className="mx-auto max-w-2xl">
            {templates.length > 0 && !canRevise && (
              <div className="mb-2 flex items-center gap-2 text-xs text-neutral-500">
                <span>整理模板：</span>
                <select
                  className="rounded border border-neutral-300 px-2 py-1 text-xs"
                  value={templateId ?? ""}
                  onChange={(e) => setTemplateId(e.target.value ? Number(e.target.value) : null)}
                >
                  <option value="">不使用模板</option>
                  {templates.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </select>
                <span className="text-neutral-400">可在「设置」中管理</span>
              </div>
            )}
            <div className="flex items-end gap-2">
              <textarea
                className="h-11 min-h-11 flex-1 resize-none rounded-lg border border-neutral-300 px-4 py-2.5 text-sm focus:border-neutral-500 focus:outline-none disabled:bg-neutral-50"
                placeholder={
                  canRevise
                    ? "直接输入修改要求，例如：安装包别动，学习资料按课程分"
                    : "输入你的文件需求：查找、提问或整理（例如：帮我整理下载文件夹）"
                }
                rows={1}
                value={input}
                disabled={planningBlocked}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send(input);
                  }
                }}
              />
              <button
                className="h-11 w-11 rounded-lg bg-neutral-900 text-white disabled:opacity-40"
                disabled={disabled}
                onClick={() => send(input)}
                aria-label="发送"
              >
                ↑
              </button>
            </div>
            {canRevise && (
              <p className="mt-1 text-xs text-neutral-400">
                当前有待确认计划：输入将被视为修改要求；点「新建任务」改为全新任务。
              </p>
            )}
          </div>
        </div>
      </div>
      <div
        className="w-[360px] shrink-0 border-l border-neutral-200 px-5 py-5"
        ref={planPanelRef}
      >
        {plan ? (
          <PlanPanel
            plan={plan}
            toolEvents={toolEvents}
            onPlanReplaced={setPlan}
            onCanceled={() => {
              setPlan(null);
              setMessages((m) => [
                ...m,
                { role: "assistant", content: "已取消该计划，没有改动任何文件。" },
              ]);
            }}
            onExecuted={(result) => {
              setLastExecution(result);
              // UX-04：执行后重新扫描，文件列表与磁盘一致（旧计划依据不变）
              if (workspace.scanId) {
                void api
                  .rescanWorkspace(workspace.scanId)
                  .then((info) => workspace.setWorkspace(info))
                  .catch(() => workspace.refreshFiles(workspace.scanId ?? ""));
              }
            }}
          />
        ) : (
          <div>
            <h2 className="text-lg font-bold">执行计划</h2>
            <div className="mt-4 rounded border border-dashed border-neutral-200 px-4 py-6 text-sm text-neutral-500">
              发起整理任务后，SORTA 生成的文件操作计划会显示在这里，供你预览、编辑和确认。
              查找与问答的结果只会出现在对话中，不会产生执行面板。
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
