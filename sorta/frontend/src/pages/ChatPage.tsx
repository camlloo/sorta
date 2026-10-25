import { useCallback, useEffect, useRef, useState } from "react";
import { useWorkspace } from "../App";
import PlanPanel from "../components/PlanPanel";
import {
  api,
  type Conversation,
  type ExecutionDetail,
  type JobSnapshot,
  type PlanDetail,
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
  const [input, setInput] = useState("");
  const [plan, setPlan] = useState<PlanDetail | null>(null);
  const [toolEvents, setToolEvents] = useState<string[]>(() => load(KEY.toolEvents, []));
  const [planning, setPlanning] = useState<boolean>(() => !!sessionStorage.getItem(KEY.jobId));
  const [jobId, setJobId] = useState<string | null>(() => sessionStorage.getItem(KEY.jobId));
  const [lastExecution, setLastExecution] = useState<ExecutionDetail | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templateId, setTemplateId] = useState<number | null>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  const planningBlocked = planning || !workspace.scanId || !workspace.hasApiKey;
  const disabled = planningBlocked || input.trim() === "";
  const canRevise = plan !== null && !lastExecution && !planning;

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
      return;
    }
    localStorage.setItem(KEY.conversationId, String(conversationId));
    api
      .getMessages(conversationId)
      .then((data) =>
        setMessages(data.messages.map((m) => ({ role: m.role, content: m.content }))),
      )
      .catch(() => {
        localStorage.removeItem(KEY.conversationId);
        setConversationId(null);
      });
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
    (
      snapshot: JobSnapshot,
      _mode: "new" | "revise",
      _prevPlan: PlanDetail | null,
      currentConversationId: number | null,
    ) => {
      const refresh = () => void refreshMessages(currentConversationId);
      if (snapshot.status === "completed" && snapshot.result) {
        const result = snapshot.result;
        if (result.task_type === "organize") {
          void api
            .getPlan(result.plan_id)
            .then((detail) => {
              setPlan(detail);
              sessionStorage.setItem(KEY.planId, JSON.stringify(result.plan_id));
              void workspace.refreshFiles(result.scan_id);
            })
            .then(refresh)
            .catch((err: Error) => {
              setMessages((m) => [
                ...m,
                { role: "assistant", content: `获取计划失败：${err.message}` },
              ]);
            });
        } else {
          setPlan(null);
          sessionStorage.removeItem(KEY.planId);
          refresh();
        }
      } else if (snapshot.status === "failed") {
        setMessages((m) => [
          ...m,
          { role: "assistant", content: `处理失败：${snapshot.error?.message ?? "未知错误"}` },
        ]);
        refresh();
      } else {
        setMessages((m) => [...m, { role: "assistant", content: "已取消本次任务。" }]);
      }
      setPlanning(false);
      setJobId(null);
      sessionStorage.removeItem(KEY.jobId);
    },
    [workspace, refreshMessages],
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

      // 无会话则先创建（多轮对话载体；跨关闭保留在数据库）
      let cid = conversationId;
      if (!reviseMode && cid === null) {
        const conv = await api.createConversation(workspace.scanId);
        cid = conv.id;
        setConversationId(cid);
        const list = await api.listConversations().catch(() => null);
        if (list) setConversations(list.conversations);
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
              cid,
            );
        setJobId(job_id);
        sessionStorage.setItem(KEY.jobId, job_id);
        const snapshot = await pollJob(job_id);
        finishTask(snapshot, reviseMode ? "revise" : "new", reviseMode ? prevPlan : null, cid);
      } catch (err) {
        await refreshMessages(cid).catch(() => undefined);
        setMessages((m) => [
          ...m,
          { role: "assistant", content: `处理失败：${(err as Error).message}` },
        ]);
        setPlanning(false);
        setJobId(null);
        sessionStorage.removeItem(KEY.jobId);
      }
    },
    [planningBlocked, workspace, plan, lastExecution, templateId, pollJob, finishTask, conversationId, refreshMessages],
  );

  const cancelPlanning = useCallback(async () => {
    if (jobId) await api.cancelJob(jobId).catch(() => undefined);
  }, [jobId]);

  const switchConversation = useCallback((id: number | null) => {
    setPlan(null);
    setToolEvents([]);
    setLastExecution(null);
    setConversationId(id);
  }, []);

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        {/* 顶栏：状态条 */}
        <div className="flex items-center justify-between border-b border-rule px-8 py-3">
          <div className="flex items-center gap-3 text-[13px]">
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
            >
              <option value="">新对话</option>
              {conversations.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </select>
            {conversationId !== null && (
              <button
                className="border border-rule px-2 py-1 font-mono text-[10px] transition-colors duration-100 hover:border-signal hover:text-signal"
                title="删除当前对话（不影响计划与执行历史）"
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

        <div className="min-h-0 flex-1 overflow-y-auto px-8 py-6">
          {messages.length === 0 ? (
            <div className="mx-auto max-w-2xl rise-in">
              {/* 海报式空态：排版即界面 */}
              <div className="mt-10">
                <div className="eyebrow">本地文件调度台</div>
                <h1
                  className="mt-3 text-[72px] leading-[0.95] font-extrabold tracking-[-0.04em]"
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
            <div className="mx-auto max-w-2xl space-y-5">
              {messages.map((msg, i) =>
                msg.role === "system" ? (
                  <div key={i} className="border-t border-rule pt-2 text-center font-mono text-[10px] tracking-wider text-graphite">
                    {msg.content}
                  </div>
                ) : (
                  <div key={i} className={msg.role === "user" ? "text-right" : ""}>
                    <div
                      className={`inline-block max-w-[88%] text-left text-[13.5px] leading-relaxed whitespace-pre-wrap ${
                        msg.role === "user"
                          ? "bg-ink px-4 py-2.5 text-paper"
                          : "border-l-2 border-rule-strong pl-4"
                      }`}
                    >
                      {msg.role === "assistant" && (
                        <div className="eyebrow mb-1">SORTA</div>
                      )}
                      {msg.content}
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

        {/* Composer */}
        <div className="border-t-2 border-ink px-8 py-4">
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
                    ? "输入修改要求，例如：安装包别动，学习资料按课程分"
                    : "输入需求，或按 / 聚焦——查找、提问或整理"
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
                className="h-11 shrink-0 bg-ink px-6 text-[13px] font-semibold text-paper transition-colors duration-100 hover:bg-signal disabled:opacity-30"
                disabled={disabled}
                onClick={() => send(input)}
              >
                发送 ↵
              </button>
            </div>
            {canRevise && (
              <p className="mt-1.5 font-mono text-[10px] text-graphite">
                当前有待确认计划：输入将被视为修改要求；「新建任务」改为全新任务。
              </p>
            )}
          </div>
        </div>
      </div>

      {/* 右栏：执行计划（位移台账） */}
      <div className="w-[360px] shrink-0 border-l border-rule">
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
