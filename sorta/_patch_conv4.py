"""多轮对话补丁 4：前端 api.ts + ChatPage 会话管理。执行后删除。"""

from pathlib import Path

# ---- api.ts ----
p = Path("frontend/src/services/api.ts")
src = p.read_text(encoding="utf-8")

old = '''export interface Template {'''
new = '''export interface Conversation {
  id: number;
  scan_id: string;
  title: string;
  updated_at: number;
}

export interface ChatMessageRecord {
  seq: number;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: number;
}

export interface Template {'''
assert old in src, "api types anchor"
src = src.replace(old, new, 1)

old = '''  createTask: (
    scanId: string,
    requestText: string,
    taskType: "auto" | "find" | "ask" | "organize",
    templateId: number | null,
    privacy: string,
  ) =>
    request<{ job_id: string }>("/api/v1/tasks", {
      method: "POST",
      body: JSON.stringify({
        scan_id: scanId,
        request: requestText,
        task_type: taskType,
        template_id: templateId,
        privacy,
      }),
    }),'''
new = '''  createTask: (
    scanId: string,
    requestText: string,
    taskType: "auto" | "find" | "ask" | "organize",
    templateId: number | null,
    privacy: string,
    conversationId: number | null,
  ) =>
    request<{ job_id: string }>("/api/v1/tasks", {
      method: "POST",
      body: JSON.stringify({
        scan_id: scanId,
        request: requestText,
        task_type: taskType,
        template_id: templateId,
        privacy,
        conversation_id: conversationId,
      }),
    }),
  listConversations: () =>
    request<{ conversations: Conversation[] }>("/api/v1/conversations"),
  createConversation: (scanId: string) =>
    request<Conversation>("/api/v1/conversations", {
      method: "POST",
      body: JSON.stringify({ scan_id: scanId }),
    }),
  getMessages: (conversationId: number) =>
    request<{ conversation: Conversation; messages: ChatMessageRecord[] }>(
      `/api/v1/conversations/${conversationId}/messages`,
    ),
  deleteConversation: (conversationId: number) =>
    request<{ deleted: boolean }>(`/api/v1/conversations/${conversationId}`, {
      method: "DELETE",
    }),'''
assert old in src, "createTask anchor"
src = src.replace(old, new, 1)
p.write_text(src, encoding="utf-8", newline="\n")

# ---- ChatPage ----
p = Path("frontend/src/pages/ChatPage.tsx")
src = p.read_text(encoding="utf-8")

# 1) imports + KEY
old = '''import {
  api,
  type ExecutionDetail,
  type JobSnapshot,
  type PlanDetail,
  type TaskResult,
  type Template,
} from "../services/api";'''
new = '''import {
  api,
  type Conversation,
  type ExecutionDetail,
  type JobSnapshot,
  type PlanDetail,
  type TaskResult,
  type Template,
} from "../services/api";'''
assert old in src, "imports anchor"
src = src.replace(old, new, 1)

old = '''const KEY = {
  messages: "sorta_chat_messages",
  planId: "sorta_plan_id",
  toolEvents: "sorta_tool_events",
  jobId: "sorta_job_id",
};'''
new = '''const KEY = {
  planId: "sorta_plan_id",
  toolEvents: "sorta_tool_events",
  jobId: "sorta_job_id",
  conversationId: "sorta_conversation_id",
};'''
assert old in src, "KEY anchor"
src = src.replace(old, new, 1)

# 2) state：conversationId + conversations；messages 改 DB 恢复
old = '''export default function ChatPage() {
  const workspace = useWorkspace();
  const [messages, setMessages] = useState<ChatMessage[]>(() => load(KEY.messages, []));
  const [input, setInput] = useState("");'''
new = '''export default function ChatPage() {
  const workspace = useWorkspace();
  const [conversationId, setConversationId] = useState<number | null>(() => {
    const raw = localStorage.getItem(KEY.conversationId);
    return raw ? Number(raw) : null;
  });
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");'''
assert old in src, "state anchor"
src = src.replace(old, new, 1)

# 3) 移除 sessionStorage messages 持久化；改为会话恢复
old = '''  // UX-05：状态持久化
  useEffect(() => {
    sessionStorage.setItem(KEY.messages, JSON.stringify(messages));
    sessionStorage.setItem(KEY.toolEvents, JSON.stringify(toolEvents));
  }, [messages, toolEvents]);'''
new = '''  // UX-05：工具日志持久化（消息以数据库为源，见会话恢复）
  useEffect(() => {
    sessionStorage.setItem(KEY.toolEvents, JSON.stringify(toolEvents));
  }, [toolEvents]);

  // 会话列表 + 从数据库恢复当前会话消息（跨关闭保留）
  useEffect(() => {
    if (!workspace.hasApiKey) return;
    api
      .listConversations()
      .then((data) => setConversations(data.conversations))
      .catch(() => undefined);
    const saved = localStorage.getItem(KEY.conversationId);
    if (saved && conversationId === null) {
      // 冷启动恢复
    }
  }, [workspace.hasApiKey]); // eslint-disable-line react-hooks/exhaustive-deps

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
        setMessages(
          data.messages.map((m) => ({ role: m.role, content: m.content })),
        ),
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
  }, []);'''
assert old in src, "persistence anchor"
src = src.replace(old, new, 1)

# 4) finishTask：消息改为从 DB 刷新
old = '''      if (snapshot.status === "completed" && snapshot.result) {
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
                      `${detail.summary}\\n\\n${snapshot.tool_events.length} 次只读工具调用，` +
                      `生成 ${detail.operations.length} 项操作。右侧台账待你确认——` +
                      "确认之前不会改动任何文件。" +
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
                      `计划已更新到 v${detail.version}：新增 ${d.added} 项、移除 ${d.removed} 项、` +
                      `调整 ${d.changed} 项目标。原审批已失效，请在右侧重新确认。`,
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
      }'''
new = '''      const refresh = () => void refreshMessages(currentConversationId);
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
      } else {
        setMessages((m) => [...m, { role: "assistant", content: "已取消本次任务。" }]);
      }'''
assert old in src, "finishTask anchor"
src = src.replace(old, new, 1)

# finishTask 依赖加 refreshMessages —— 修改 useCallback 依赖数组
old = '''    [workspace],
  );

  const send = useCallback('''
new = '''    [workspace, refreshMessages],
  );

  const send = useCallback('''
assert old in src, "finishTask deps"
src = src.replace(old, new, 1)

# 5) send：建/用会话 + 带 conversation_id；完成后传 id 给 finishTask
old = '''      const reviseMode = plan !== null && !lastExecution;
      const prevPlan = plan;
      if (!reviseMode) {
        setPlan(null);
        sessionStorage.removeItem(KEY.planId);
      }

      try {
        const { job_id } = reviseMode
          ? await api.revisePlan(plan!.plan_id, clean, plan!.version)
          : await api.createTask(workspace.scanId, clean, taskType, templateId, workspace.privacy);
        setJobId(job_id);
        sessionStorage.setItem(KEY.jobId, job_id);
        const snapshot = await pollJob(job_id);
        finishTask(snapshot, reviseMode ? "revise" : "new", reviseMode ? prevPlan : null);
      } catch (err) {
        setMessages((m) => [
          ...m,
          { role: "user", content: clean },
          { role: "assistant", content: `处理失败：${(err as Error).message}` },
        ]);
        setPlanning(false);
        setJobId(null);
        sessionStorage.removeItem(KEY.jobId);
      }'''
new = '''      const reviseMode = plan !== null && !lastExecution;
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
        finishTaskWith(snapshot, reviseMode ? "revise" : "new", reviseMode ? prevPlan : null, cid);
      } catch (err) {
        await refreshMessages(cid).catch(() => undefined);
        setMessages((m) => [
          ...(m.length === 0 || m[m.length - 1].content !== clean ? [{ role: "user" as const, content: clean }] : []),
          { role: "assistant", content: `处理失败：${(err as Error).message}` },
        ]);
        setPlanning(false);
        setJobId(null);
        sessionStorage.removeItem(KEY.jobId);
      }'''
assert old in src, "send anchor"
src = src.replace(old, new, 1)

# send 内 finishTask 调用改为支持 cid 的包装：直接给 finishTask 加第 4 参更简单——改 finishTask 签名
old = '''  const finishTask = useCallback(
    (snapshot: JobSnapshot, mode: "new" | "revise", prevPlan: PlanDetail | null) => {'''
new = '''  const finishTask = useCallback(
    (
      snapshot: JobSnapshot,
      mode: "new" | "revise",
      prevPlan: PlanDetail | null,
      currentConversationId: number | null,
    ) => {'''
assert old in src, "finishTask signature"
src = src.replace(old, new, 1)

# 提供别名供旧调用
old = '''  const send = useCallback('''
new = '''  const finishTaskWith = finishTask;

  const send = useCallback('''
assert old in src, "alias anchor"
src = src.replace(old, new, 1)

# send 依赖数组加 conversationId
old = '''    [planningBlocked, workspace, plan, lastExecution, templateId, pollJob, finishTask],
  );'''
new = '''    [planningBlocked, workspace, plan, lastExecution, templateId, pollJob, finishTask, conversationId, refreshMessages],
  );'''
assert old in src, "send deps"
src = src.replace(old, new, 1)

# 6) startNewTask：改为新会话 + 会话切换器 UI（顶栏）
old = '''  const startNewTask = useCallback(() => {
    setPlan(null);
    setToolEvents([]);
    setLastExecution(null);
    setMessages((m) => [
      ...m,
      { role: "system", content: "— 已开始新任务（原计划保留在数据库中，可在历史中查看）—" },
    ]);
  }, []);'''
new = '''  const startNewTask = useCallback(() => {
    setPlan(null);
    setToolEvents([]);
    setLastExecution(null);
    // 新对话：新会话（旧会话保留在数据库，可从顶栏切换回来）
    setConversationId(null);
  }, []);

  const switchConversation = useCallback(
    (id: number | null) => {
      setPlan(null);
      setToolEvents([]);
      setLastExecution(null);
      setConversationId(id);
    },
    [],
  );'''
assert old in src, "startNewTask anchor"
src = src.replace(old, new, 1)

# 7) 顶栏 UI：加会话选择器
old = '''          {canRevise && (
            <button
              className="border border-rule px-2 py-1 text-[11px] font-medium transition-colors duration-100 hover:border-ink hover:bg-ink hover:text-paper"
              onClick={startNewTask}
            >
              新建任务
            </button>
          )}'''
new = '''          <div className="flex items-center gap-2">
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
            {canRevise && (
              <button
                className="border border-rule px-2 py-1 text-[11px] font-medium transition-colors duration-100 hover:border-ink hover:bg-ink hover:text-paper"
                onClick={startNewTask}
              >
                新建任务
              </button>
            )}
          </div>'''
assert old in src, "topbar anchor"
src = src.replace(old, new, 1)

p.write_text(src, encoding="utf-8", newline="\n")
print("ChatPage patched")
