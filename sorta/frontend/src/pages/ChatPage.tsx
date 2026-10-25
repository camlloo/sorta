import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useWorkspace } from "../App";
import Markdown from "../components/Markdown";
import PlanPanel from "../components/PlanPanel";
import {
  api,
  type Conversation,
  type ExecutionDetail,
  type JobSnapshot,
  type PlanDetail,
  type StreamEvent,
  type Template,
} from "../services/api";

interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

/** 对话页：海报式空态 + 命令行列表（hover 整行反色）+ 对话流 + composer。
 * 状态持久化与任务分流逻辑见 EXT-02 / UX-05。 */

const KEY = {
  planId: "sorta_plan_id",
  toolEvents: "sorta_tool_events",
  jobId: "sorta_job_id",
  conversationId: "sorta_conversation_id",
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

const COMMANDS = [
  {
    title: "查找文件",
    example: "帮我找一下和 Postman 作业有关的文件",
    taskType: "find" as const,
  },
  {
    title: "整理文件",
    example: "把与安全学习相关的资料整理到 Security-Learning",
    taskType: "organize" as const,
  },
  {
    title: "了解目录",
    example: "这个目录里大概是什么用途的文件？",
    taskType: "ask" as const,
  },
  {
    title: "自由指令",
    example: "帮我把安装包移到 Installers 目录，其他文件别动",
    taskType: "auto" as const,
  },
];

export default function ChatPage() {
  const workspace = useWorkspace();
  const [conversationId, setConversationId] = useState<number | null>(() => {
    const raw = localStorage.getItem(KEY.conversationId);
    return raw ? Number(raw) : null;
  });
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loadingConversation, setLoadingConversation] = useState(() => localStorage.getItem(KEY.conversationId) !== null);
  const [input, setInput] = useState("");
  const [plan, setPlanState] = useState<PlanDetail | null>(null);
  const setPlan = useCallback((detail: PlanDetail | null) => {
    setPlanState(detail);
    if (detail) sessionStorage.setItem(KEY.planId, JSON.stringify(detail.plan_id));
    else sessionStorage.removeItem(KEY.planId);
  }, []);
  const [toolEvents, setToolEvents] = useState<string[]>(() => load(KEY.toolEvents, []));
  const [planning, setPlanning] = useState<boolean>(() => !!sessionStorage.getItem(KEY.jobId));
  const [jobId, setJobId] = useState<string | null>(() => sessionStorage.getItem(KEY.jobId));
  const abortRef = useRef<AbortController | null>(null);
  const lifecycleRef = useRef(new AbortController());
  const [lastExecution, setLastExecution] = useState<ExecutionDetail | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templateId, setTemplateId] = useState<number | null>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const followLatest = useRef(true);
  const contentHeight = useRef(0);

  useLayoutEffect(() => {
    const container = scrollRef.current;
    if (container && followLatest.current) {
      container.scrollTop = container.scrollHeight;
      contentHeight.current = container.scrollHeight;
    }
  }, [messages, planning]);

  useEffect(() => {
    const container = scrollRef.current;
    const content = contentRef.current;
    if (!container || !content) return;
    const observer = new ResizeObserver(() => {
      if (followLatest.current) container.scrollTop = container.scrollHeight;
      contentHeight.current = container.scrollHeight;
    });
    observer.observe(content);
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    lifecycleRef.current = controller;
    return () => {
      controller.abort();
      abortRef.current?.abort();
    };
  }, []);

  const planningBlocked = planning || loadingConversation || !workspace.scanId || !workspace.hasApiKey;
  const disabled = planningBlocked || input.trim() === "";
  const canRevise = plan !== null && plan.operations.length > 0 &&
    ["draft", "validated", "awaiting_approval", "approved", "stale"].includes(plan.status) &&
    !lastExecution && !planning;

  // 「/」快捷聚焦输入框
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "/" && document.activeElement?.tagName !== "TEXTAREA" && document.activeElement?.tagName !== "INPUT") {
        e.preventDefault();
        composerRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // UX-05：工具日志持久化；消息以数据库为源（跨关闭保留）
  useEffect(() => {
    sessionStorage.setItem(KEY.toolEvents, JSON.stringify(toolEvents));
  }, [toolEvents]);

  useEffect(() => {
    if (!workspace.hasApiKey) return;
    api
      .listConversations()
      .then((data) => setConversations(data.conversations))
      .catch(() => undefined);
  }, [workspace.hasApiKey]);

  useEffect(() => {
    if (conversationId === null) {
      localStorage.removeItem(KEY.conversationId);
      setMessages([]);
      setLoadingConversation(false);
      return;
    }
    localStorage.setItem(KEY.conversationId, String(conversationId));
    if (planning) return;
    let active = true;
    api
      .getMessages(conversationId)
      .then(async (data) => {
        if (!active) return;
        setMessages(data.messages.map((m) => ({ role: m.role, content: m.content })));
        if (data.conversation.plan_id) {
          const detail = await api.getPlan(data.conversation.plan_id).catch(() => null);
          if (active) setPlan(detail);
        }
      })
      .catch((err: Error & { status?: number }) => {
        if (!active) return;
        if (err.status === 404) {
          localStorage.removeItem(KEY.conversationId);
          setConversationId(null);
        } else {
          setMessages((m) => [...m, { role: "assistant", content: `读取对话失败，请刷新重试：${err.message}` }]);
        }
      })
      .finally(() => { if (active) setLoadingConversation(false); });
    return () => { active = false; };
    // 任务执行时保留乐观消息；完成时由 finishTask 刷新。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  const refreshMessages = useCallback(async (cid: number | null) => {
    if (cid === null) return;
    try {
      const data = await api.getMessages(cid);
      setMessages(data.messages.map((m) => ({ role: m.role, content: m.content })));
      const list = await api.listConversations();
      setConversations(list.conversations);
    } catch {
      /* 会话被删等场景静默 */
    }
  }, []);

  useEffect(() => {
    if (workspace.hasApiKey && templates.length === 0) {
      api
        .listTemplates()
        .then((data) => setTemplates(data.templates.filter((t) => t.enabled)))
        .catch(() => undefined);
    }
  }, [workspace.hasApiKey, templates.length]);

  const pollJob = useCallback(
    async (id: string, signal?: AbortSignal): Promise<JobSnapshot> => {
      for (;;) {
        await new Promise((r) => setTimeout(r, 1500));
        signal?.throwIfAborted();
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
    async (
      snapshot: JobSnapshot,
      mode: "new" | "revise",
      _prevPlan: PlanDetail | null,
      currentConversationId: number | null,
    ) => {
      if (snapshot.status === "completed" && snapshot.result) {
        const result = snapshot.result;
        if (result.task_type === "organize") {
          const detail = await api.getPlan(result.plan_id);
          setPlan(detail);
          setLastExecution(null);
          await refreshMessages(currentConversationId);
          if (mode === "revise" && currentConversationId === null) {
            setMessages((m) => [...m, {
              role: "assistant", content: `### 计划已更新\n\n**v${detail.version}**\n\n${detail.summary}`,
            }]);
          }
        } else {
          await refreshMessages(currentConversationId);
        }
      } else if (snapshot.status === "failed") {
        await refreshMessages(currentConversationId);
        setMessages((m) => [
          ...m,
          { role: "assistant", content: `处理失败：${snapshot.error?.message ?? "未知错误"}` },
        ]);
      } else {
        setMessages((m) => [...m, { role: "assistant", content: "已取消本次任务。" }]);
      }
      setPlanning(false);
      setLoadingConversation(false);
      setJobId(null);
      sessionStorage.removeItem(KEY.jobId);
    },
    [refreshMessages, setPlan],
  );

  // 刷新或切回页面后继续处理任务结果，StrictMode 清理旧轮询。
  useEffect(() => {
    const controller = new AbortController();
    const restore = async () => {
      try {
        const id = sessionStorage.getItem(KEY.jobId);
        if (id) {
          const snapshot = await pollJob(id, controller.signal);
          controller.signal.throwIfAborted();
          await finishTask(snapshot, snapshot.kind === "plan-revision" ? "revise" : "new",
            null, conversationId);
        }
      } catch (err) {
        if (controller.signal.aborted) return;
        setMessages((m) => [...m, {
          role: "assistant", content: `恢复失败：${(err as Error).message}`,
        }]);
        setPlanning(false);
        setLoadingConversation(false);
        setJobId(null);
        sessionStorage.removeItem(KEY.jobId);
      }
    };
    void restore();
    return () => controller.abort();
    // 仅在页面挂载时恢复；后续任务由 send 管理。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const send = useCallback(
    async (text: string, taskType: "auto" | "find" | "ask" | "organize" = "auto") => {
      const clean = text.trim();
      if (!clean || planningBlocked || !workspace.scanId) return;
      followLatest.current = true;
      setMessages((m) => [...m, { role: "user", content: clean }]);
      setInput("");
      setPlanning(true);
      setToolEvents([]);
      abortRef.current = new AbortController();

      const activePlan = canRevise ? plan : null;

      // 无会话则先创建（多轮对话载体；跨关闭保留在数据库）
      let cid = conversationId;
      const lifecycle = lifecycleRef.current.signal;
      try {
        if (cid === null) {
          const conv = await api.createConversation(workspace.scanId);
          cid = conv.id;
          lifecycle.throwIfAborted();
          abortRef.current?.signal.throwIfAborted();
          setConversationId(cid);
          const list = await api.listConversations().catch(() => null);
          if (list) setConversations(list.conversations);
        }

        const runJob = async (explicitType: "auto" | "find" | "ask" | "organize") => {
          if (!workspace.scanId) return;
          abortRef.current?.signal.throwIfAborted();
          const { job_id } = await api.createTask(
            workspace.scanId,
            clean,
            explicitType,
            templateId,
            workspace.privacy,
            cid,
          );
          setJobId(job_id);
          sessionStorage.setItem(KEY.jobId, job_id);
          if (abortRef.current?.signal.aborted) await api.cancelJob(job_id);
          const snapshot = await pollJob(job_id, lifecycle);
          await finishTask(snapshot, "new", null, cid);
        };

        const patchLast = (content: string) =>
          setMessages((m) => {
            const next = [...m];
            if (next.length > 0 && next[next.length - 1].role === "assistant") {
              next[next.length - 1] = { role: "assistant", content };
            } else {
              next.push({ role: "assistant", content });
            }
            return next;
          });

        if (taskType === "find" || taskType === "organize") {
          await runJob(taskType);
        } else {
          // auto / ask：流式（SSE）——分类在服务端完成，organize 自动转 job
          setMessages((m) => [...m, { role: "assistant", content: "" }]);
          let streamed = "";
          let redirected = false;
          let revision: { plan_id: number; version: number } | null = null;
          await api.streamTask(
            {
              scan_id: workspace.scanId,
              request: clean,
              task_type: taskType,
              template_id: templateId,
              conversation_id: cid,
              active_plan_id: activePlan?.plan_id ?? null,
            },
            (event: StreamEvent) => {
              if ("conversation_id" in event && event.conversation_id !== null) {
                cid = event.conversation_id;
                localStorage.setItem(KEY.conversationId, String(cid));
                setConversationId(cid);
              }
              if (event.type === "delta") {
                streamed += event.text;
                patchLast(streamed);
              } else if (event.type === "done") {
                patchLast(event.answer || "（模型未返回内容）");
              } else if (event.type === "clarify") {
                patchLast(event.question);
              } else if (event.type === "find") {
                const r = event.result;
                const lines =
                  r.matches.length === 0
                    ? ["没有匹配的文件。"]
                    : r.matches.map((mm) => `${mm.path} — ${mm.reason}`);
                patchLast(`${r.note}\n${lines.join("\n")}`);
              } else if (event.type === "organize") {
                redirected = true;
              } else if (event.type === "revise") {
                revision = { plan_id: event.plan_id, version: event.version };
              }
            },
            abortRef.current?.signal,
          );
          if (redirected || revision) {
            setMessages((m) => {
              const next = [...m];
              if (next.length > 0 && next[next.length - 1].role === "assistant") next.pop();
              return next;
            });
            if (revision) {
              const target = revision as { plan_id: number; version: number };
              const { job_id } = await api.revisePlan(target.plan_id, clean, target.version, cid);
              setJobId(job_id);
              sessionStorage.setItem(KEY.jobId, job_id);
              if (abortRef.current?.signal.aborted) await api.cancelJob(job_id);
              const snapshot = await pollJob(job_id, lifecycle);
              await finishTask(snapshot, "revise", activePlan, cid);
            } else {
              await runJob("organize");
            }
          } else {
            // ask/clarify/find 流式完成：复位输入状态（会话消息已由服务端落库）
            setPlanning(false);
            setJobId(null);
            sessionStorage.removeItem(KEY.jobId);
            const list = await api.listConversations().catch(() => null);
            if (list) setConversations(list.conversations);
          }
        }
      } catch (err) {
        if (lifecycle.aborted) return;
        if ((err as Error).name === "AbortError") {
          setMessages((m) => [...m, { role: "system", content: "— 已取消 —" }]);
        } else {
          await refreshMessages(cid).catch(() => undefined);
          setMessages((m) => [
            ...m,
            { role: "assistant", content: `处理失败：${(err as Error).message}` },
          ]);
        }
        setPlanning(false);
        setJobId(null);
        sessionStorage.removeItem(KEY.jobId);
      }
    },
    [planningBlocked, workspace, plan, canRevise, templateId, pollJob, finishTask, conversationId, refreshMessages],
  );

  const cancelPlanning = useCallback(async () => {
    abortRef.current?.abort();
    if (jobId) await api.cancelJob(jobId).catch(() => undefined);
  }, [jobId]);

  const switchConversation = useCallback((id: number | null) => {
    followLatest.current = true;
    setPlan(null);
    setToolEvents([]);
    setLastExecution(null);
    setConversationId(id);
    setMessages([]);
    setLoadingConversation(id !== null);
  }, [setPlan]);

  return (
    <div className="flex h-full min-h-0 flex-col xl:flex-row">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {/* 顶栏：状态条 */}
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-rule px-4 py-3 sm:px-8">
          <div className="flex flex-wrap items-center gap-3 text-[13px]">
            <span className="font-semibold">{workspace.model || "未配置模型"}</span>
            {!workspace.hasApiKey && (
              <span className="border-b-2 border-signal pb-0.5 text-signal">未配置 API Key</span>
            )}
            {workspace.privacy === "metadata_only" && (
              <span className="font-mono text-[10px] text-graphite">PRIVACY / METADATA-ONLY</span>
            )}
            {workspace.privacy === "ask_before_content" && (
              <span className="font-mono text-[10px] text-graphite">PRIVACY / ASK-FIRST</span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <select
              className="max-w-[180px] truncate border border-rule px-2 py-1 font-mono text-[11px] focus:border-ink focus:outline-none"
              value={conversationId ?? ""}
              onChange={(e) =>
                switchConversation(e.target.value ? Number(e.target.value) : null)
              }
              title="对话历史（保存在本机数据库）"
              disabled={planning}
            >
              <option value="">新对话</option>
              {conversations.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </select>
            <button
              className="border border-rule px-2 py-1 text-[11px] disabled:opacity-40"
              disabled={planning}
              onClick={() => switchConversation(null)}
            >
              新对话
            </button>
            {conversationId !== null && (
              <button
                className="border border-rule px-2 py-1 font-mono text-[10px] transition-colors duration-100 hover:border-signal hover:text-signal"
                title="删除当前对话（不影响计划与执行历史）"
                disabled={planning}
                onClick={async () => {
                  if (!window.confirm("删除当前对话记录？此操作不可恢复。")) return;
                  await api.deleteConversation(conversationId).catch(() => undefined);
                  const list = await api.listConversations().catch(() => null);
                  if (list) setConversations(list.conversations);
                  switchConversation(null);
                }}
              >
                删除
              </button>
            )}
          </div>
        </div>
        {planning && <div className="marquee-line" aria-hidden />}

        <div
          ref={scrollRef}
          className="conversation-scroll min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-8"
          onScroll={(event) => {
            const container = event.currentTarget;
            if (container.scrollHeight === contentHeight.current) {
              followLatest.current = container.scrollHeight - container.scrollTop - container.clientHeight < 80;
            }
            contentHeight.current = container.scrollHeight;
          }}
        >
          <div ref={contentRef}>
          {messages.length === 0 ? (
            <div className="mx-auto max-w-2xl rise-in">
              {/* 海报式空态：排版即界面 */}
              <div className="mt-10">
                <div className="eyebrow">本地文件调度台</div>
                <h1
                  className="mt-3 text-[clamp(42px,8vw,72px)] leading-[0.95] font-extrabold tracking-[-0.04em]"
                  style={{ fontWeight: 850 }}
                >
                  把混乱
                  <br />
                  变成秩序<span className="text-signal">.</span>
                </h1>
              </div>

              {/* 命令行列表：hover 整行反色 */}
              <div className="mt-10 border-t border-rule">
                {COMMANDS.map((cmd) => (
                  <button
                    key={cmd.title}
                    className="invert-hover flex w-full items-baseline gap-6 border-b border-rule px-4 py-4 text-left disabled:opacity-40"
                    disabled={planningBlocked}
                    onClick={() => send(cmd.example, cmd.taskType)}
                  >
                    <span className="w-20 shrink-0 text-[14px] font-semibold">{cmd.title}</span>
                    <span className="invert-dim font-mono text-[12px] leading-relaxed">
                      {cmd.example}
                    </span>
                  </button>
                ))}
              </div>
              {!workspace.scanId && (
                <p className="mt-4 font-mono text-[11px] text-graphite">
                  先在「设置」中选择工作目录。
                </p>
              )}
            </div>
          ) : (
            <div className="mx-auto max-w-2xl space-y-9">
              {messages.map((msg, i) =>
                msg.role === "system" ? (
                  <div key={i} className="border-t border-rule pt-2 text-center font-mono text-[10px] tracking-wider text-graphite">
                    {msg.content}
                  </div>
                ) : (
                  <div key={i} className={msg.role === "user" ? "text-right" : ""}>
                    <div
                      className={`text-left ${
                        msg.role === "user"
                          ? "inline-block max-w-[88%] whitespace-pre-wrap bg-ink px-4 py-3 text-[14px] leading-relaxed text-paper"
                          : "w-full min-w-0 py-1"
                      }`}
                    >
                      {msg.role === "assistant" && (
                        <div className="mb-3 text-[11px] font-semibold tracking-[0.14em] text-graphite">SORTA</div>
                      )}
                      {msg.role === "assistant" ? (
                        msg.content ? (
                          <Markdown text={msg.content} />
                        ) : (
                          <span className="animate-pulse text-graphite">…</span>
                        )
                      ) : (
                        msg.content
                      )}
                    </div>
                  </div>
                ),
              )}
              {planning && (
                <div className="border-l-2 border-signal pl-4">
                  <div className="text-[13px]">
                    <span className="font-semibold">
                      {canRevise || plan ? "正在更新计划…" : "SORTA 正在调查…"}
                    </span>{" "}
                    <span className="font-mono text-[11px] text-graphite">
                      {toolEvents.length > 0 ? `已执行 ${toolEvents.length} 次只读调用` : "…"}
                    </span>
                  </div>
                  {toolEvents.length > 0 && (
                    <div className="mt-1 max-h-28 overflow-y-auto font-mono text-[11px] text-graphite">
                      {toolEvents.map((event, i) => (
                        <div key={i}>{event}</div>
                      ))}
                    </div>
                  )}
                  <button
                    className="mt-1 text-[11px] text-signal underline-offset-2 hover:underline"
                    onClick={cancelPlanning}
                  >
                    取消（在安全停点生效）
                  </button>
                </div>
              )}
              {lastExecution && (
                <div className="font-mono text-[11px] text-graphite">
                  最近执行：{lastExecution.status} / {lastExecution.execution_id}
                </div>
              )}
            </div>
          )}
          </div>
        </div>

        {/* Composer */}
        <div className="border-t-2 border-ink px-4 py-4 sm:px-8">
          <div className="mx-auto max-w-2xl">
            {templates.length > 0 && !canRevise && (
              <div className="mb-2 flex items-center gap-2 text-[11px] text-graphite">
                <span className="eyebrow">模板</span>
                <select
                  className="border border-rule px-2 py-1 font-mono text-[11px] focus:border-ink focus:outline-none"
                  value={templateId ?? ""}
                  onChange={(e) => setTemplateId(e.target.value ? Number(e.target.value) : null)}
                >
                  <option value="">不使用</option>
                  {templates.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <div className="flex items-end gap-3">
              <textarea
                ref={composerRef}
                className="h-12 min-h-12 flex-1 resize-none border-b border-rule-strong bg-transparent px-1 py-2.5 text-[14px] leading-relaxed transition-colors duration-100 focus:border-signal focus:outline-none disabled:opacity-40"
                placeholder={
                  canRevise
                    ? "继续补充要求，或提出新的任务"
                    : "输入需求，或按 / 聚焦——查找、提问或整理"
                }
                rows={1}
                value={input}
                disabled={planningBlocked}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    void send(input);
                  }
                }}
              />
              <button
                className="h-11 shrink-0 bg-ink px-6 text-[13px] font-semibold text-paper transition-colors duration-100 hover:bg-signal disabled:opacity-30"
                disabled={disabled}
                onClick={() => send(input)}
              >
                发送 ↵
              </button>
            </div>
            {canRevise && (
              <p className="mt-1.5 font-mono text-[10px] text-graphite">
                当前有待确认计划。可以继续修改、提问，或提出新任务；本轮沿用对话上下文。
              </p>
            )}
          </div>
        </div>
      </div>

      {/* 右栏：执行计划（位移台账） */}
      <div className={`${plan ? "max-h-[45%] overflow-y-auto" : "hidden xl:block"} w-full shrink-0 border-t border-rule xl:max-h-none xl:w-[360px] xl:border-t-0 xl:border-l`}>
        {plan ? (
          <PlanPanel
            key={`${plan.plan_id}:${plan.version}`}
            plan={plan}
            toolEvents={toolEvents}
            onPlanReplaced={setPlan}
            onUndone={(result) => {
              setLastExecution(result);
              void refreshMessages(conversationId);
              if (workspace.scanId) {
                void api.rescanWorkspace(workspace.scanId)
                  .then(workspace.setWorkspace)
                  .catch(() => undefined);
              }
            }}
            onCanceled={() => {
              setPlan(null);
              setMessages((m) => [
                ...m,
                { role: "assistant", content: "已取消该计划，没有改动任何文件。" },
              ]);
            }}
            onExecuted={(result) => {
              setLastExecution(result);
              void refreshMessages(conversationId);
              if (workspace.scanId) {
                void api
                  .rescanWorkspace(workspace.scanId)
                  .then((info) => workspace.setWorkspace(info))
                  .catch(() => workspace.refreshFiles(workspace.scanId ?? ""));
              }
            }}
          />
        ) : (
          <div className="px-6 py-8">
            <div className="eyebrow">执行计划</div>
            <p className="mt-3 text-[13px] leading-relaxed text-graphite">
              整理任务的操作台账——确认后才执行。
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
