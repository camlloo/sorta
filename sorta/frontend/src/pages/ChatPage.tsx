import { useRef, useState } from "react";
import { useWorkspace } from "../App";
import PlanPanel from "../components/PlanPanel";
import { api, CAPABILITIES, type ExecutionDetail, type PlanDetail } from "../services/api";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export default function ChatPage() {
  const workspace = useWorkspace();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [plan, setPlan] = useState<PlanDetail | null>(null);
  const [toolEvents, setToolEvents] = useState<string[]>([]);
  const [planning, setPlanning] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [lastExecution, setLastExecution] = useState<ExecutionDetail | null>(null);
  const planPanelRef = useRef<HTMLDivElement>(null);

  const planningBlocked = planning || !workspace.scanId || !workspace.hasApiKey;
  const disabled = planningBlocked || input.trim() === "";  // 输入框发送键用
  const cardsDisabled = planningBlocked;  // 能力卡片不受输入框内容影响

  async function send(text: string) {
    const clean = text.trim();
    if (!clean || planning || !workspace.scanId) return;
    setMessages((m) => [...m, { role: "user", content: clean }]);
    setInput("");
    setPlanning(true);
    setToolEvents([]);
    setPlan(null);
    setLastExecution(null);
    try {
      const { job_id } = await api.createPlan(workspace.scanId, clean, workspace.privacy);
      setJobId(job_id);
      // 轮询 Job 状态（v2 §4.4：0.5-2 秒间隔）
      for (;;) {
        await new Promise((r) => setTimeout(r, 1500));
        const job = await api.getJob(job_id);
        setToolEvents(job.tool_events);
        if (job.status === "completed" && job.result) {
          const detail = await api.getPlan(job.result.plan_id);
          setPlan(detail);
          const uncertain = detail.uncertain_names.length
            ? `另有 ${detail.uncertain_names.length} 个文件证据不足，已列入待确认。`
            : "";
          setMessages((m) => [
            ...m,
            {
              role: "assistant",
              content:
                `${detail.summary}\n\n共 ${job.tool_events.length} 次只读工具调用，` +
                `生成 ${detail.operations.length} 项操作；右侧可预览、编辑并确认执行。${uncertain}`,
            },
          ]);
          // 计划生成会产生新的扫描会话（文件已移动），刷新工作区
          void workspace.refreshFiles(job.result.scan_id);
          break;
        }
        if (job.status === "failed") {
          setMessages((m) => [
            ...m,
            {
              role: "assistant",
              content: `调查失败：${job.error?.message ?? "未知错误"}`,
            },
          ]);
          break;
        }
        if (job.status === "canceled") {
          setMessages((m) => [...m, { role: "assistant", content: "已取消本次调查。" }]);
          break;
        }
      }
    } catch (err) {
      setMessages((m) => [
        ...m,
        { role: "assistant", content: `调查失败：${(err as Error).message}` },
      ]);
    } finally {
      setPlanning(false);
      setJobId(null);
      planPanelRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }

  async function cancelPlanning() {
    if (jobId) await api.cancelJob(jobId).catch(() => undefined);
  }

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="border-b border-neutral-200 px-8 py-3 text-sm">
          <span className="font-semibold">SORTA</span>
          <span className="ml-2 text-neutral-400">· {workspace.model || "未配置模型"}</span>
          {!workspace.hasApiKey && <span className="ml-2 text-red-600">· 未配置 API Key</span>}
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
                    disabled={cardsDisabled}
                    onClick={() => send(card.example)}
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
              {messages.map((msg, i) => (
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
              ))}
              {planning && (
                <div className="text-sm text-neutral-500">
                  <span className="animate-pulse">SORTA 正在调查…</span>
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
                    取消
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
          <div className="mx-auto flex max-w-2xl items-end gap-2">
            <textarea
              className="h-11 min-h-11 flex-1 resize-none rounded-lg border border-neutral-300 px-4 py-2.5 text-sm focus:border-neutral-500 focus:outline-none disabled:bg-neutral-50"
              placeholder="输入你的文件需求，例如：帮我整理下载文件夹"
              rows={1}
              value={input}
              disabled={planning || !workspace.scanId || !workspace.hasApiKey}
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
              void workspace.refreshFiles(workspace.scanId ?? "");
            }}
          />
        ) : (
          <div>
            <h2 className="text-lg font-bold">执行计划</h2>
            <div className="mt-4 rounded border border-dashed border-neutral-200 px-4 py-6 text-sm text-neutral-500">
              发起对话后，SORTA 生成的文件操作计划会显示在这里，供你预览、编辑和确认。
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
