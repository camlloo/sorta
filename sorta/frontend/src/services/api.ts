/**
 * SORTA API 客户端（v2 文档 §4 路由契约）。
 * 错误统一为 {code, message, details, request_id} 信封；前端按 code 决定反馈。
 */

export interface SessionInfo {
  token: string;
  model: string;
  base_url: string;
  has_api_key: boolean;
}

export interface FileRow {
  file_id: string;
  path: string;
  extension: string;
  size: number;
  modified_ns: number;
}

export interface WorkspaceInfo {
  scan_id: string;
  root: string;
  file_count: number;
  created_at: number;
  expires_at: number;
}

export interface Issue {
  level: "error" | "warning";
  operation_index: number | null;
  code: string;
  message: string;
}

export interface PlanOperation {
  source_id: string;
  destination: string;
  action: "move" | "copy";
  confidence: "high" | "medium" | "low";
  reason: string;
}

export interface PlanDetail {
  plan_id: number;
  scan_id: string;
  version: number;
  plan_hash: string;
  status: string;
  summary: string;
  operations: PlanOperation[];
  uncertain_files: string[];
  uncertain_names: string[];
  issues: Issue[];
  has_errors: boolean;
}

export interface JobSnapshot {
  job_id: string;
  kind: string;
  status: "queued" | "running" | "completed" | "failed" | "canceled";
  result: { plan_id: number; scan_id: string; version: number; plan_hash: string } | null;
  error: { code: string; message: string } | null;
  tool_events: string[];
}

export interface ExecutionOperation {
  index: number;
  source: string;
  destination: string;
  action: string;
  status: string;
  error: string | null;
}

export interface ExecutionDetail {
  execution_id: string;
  status: string;
  started_at: number;
  finished_at: number | null;
  operations: ExecutionOperation[];
}

export interface HistoryEntry {
  execution_id: string;
  status: string;
  started_at: number;
  finished_at: number | null;
}

export interface Settings {
  root: string;
  privacy: string;
  model: string;
  base_url: string;
  has_api_key: boolean;
  api_key_hint: string;
  scan_session_ttl_seconds: number;
  database_url: string;
}

export class ApiError extends Error {
  code: string;
  status: number;
  details: Record<string, unknown>;

  constructor(code: string, message: string, status: number, details: Record<string, unknown>) {
    super(message);
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

let token = sessionStorage.getItem("sorta_token") ?? "";

export async function bootstrapSession(): Promise<SessionInfo> {
  const res = await fetch("/api/v1/session");
  if (!res.ok) throw new ApiError("SESSION_FAILED", "无法连接 SORTA 服务", res.status, {});
  const data = (await res.json()) as SessionInfo;
  token = data.token;
  sessionStorage.setItem("sorta_token", token);
  return data;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-SORTA-Token": token,
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as {
      code?: string;
      message?: string;
      details?: Record<string, unknown>;
    };
    throw new ApiError(
      body.code ?? "HTTP_ERROR",
      body.message ?? res.statusText,
      res.status,
      body.details ?? {},
    );
  }
  return res.json() as Promise<T>;
}

export const api = {
  createWorkspace: (root: string) =>
    request<WorkspaceInfo>("/api/v1/workspaces", {
      method: "POST",
      body: JSON.stringify({ root }),
    }),
  listFiles: (scanId: string, query = "") =>
    request<{ scan_id: string; count: number; files: FileRow[] }>(
      `/api/v1/scans/${scanId}/files?query=${encodeURIComponent(query)}`,
    ),
  previewFile: (scanId: string, fileId: string) =>
    request<{ status: string; content: string | null; error: string | null }>(
      `/api/v1/scans/${scanId}/files/${fileId}/preview`,
      { method: "POST" },
    ),
  summarizeFile: (scanId: string, fileId: string) =>
    request<{ summary: string }>(`/api/v1/scans/${scanId}/files/${fileId}/summary`, {
      method: "POST",
    }),
  findDuplicates: (scanId: string) =>
    request<{ groups: string[][] }>(`/api/v1/scans/${scanId}/duplicates`, { method: "POST" }),
  createPlan: (scanId: string, requestText: string, privacy: string) =>
    request<{ job_id: string }>("/api/v1/plans", {
      method: "POST",
      body: JSON.stringify({ scan_id: scanId, request: requestText, privacy }),
    }),
  getJob: (jobId: string) => request<JobSnapshot>(`/api/v1/jobs/${jobId}`),
  cancelJob: (jobId: string) =>
    request<{ canceled: boolean }>(`/api/v1/jobs/${jobId}/cancel`, { method: "POST" }),
  getPlan: (planId: number) => request<PlanDetail>(`/api/v1/plans/${planId}`),
  editPlan: (
    planId: number,
    body: { keep: boolean[]; destinations: string[]; expected_version: number },
  ) => request<PlanDetail>(`/api/v1/plans/${planId}`, { method: "PATCH", body: JSON.stringify(body) }),
  validatePlan: (planId: number) =>
    request<{ issues: Issue[]; has_errors: boolean }>(`/api/v1/plans/${planId}/validate`, {
      method: "POST",
    }),
  approvePlan: (planId: number, expected_version: number, expected_plan_hash: string) =>
    request<{ approval_id: number; plan_hash: string; version: number }>(
      `/api/v1/plans/${planId}/approve`,
      { method: "POST", body: JSON.stringify({ expected_version, expected_plan_hash }) },
    ),
  rejectPlan: (planId: number) =>
    request<{ rejected: boolean }>(`/api/v1/plans/${planId}/reject`, { method: "POST" }),
  execute: (approvalId: number) =>
    request<ExecutionDetail>("/api/v1/executions", {
      method: "POST",
      body: JSON.stringify({ approval_id: approvalId }),
    }),
  undo: (executionId: string) =>
    request<ExecutionDetail>(`/api/v1/executions/${executionId}/undo`, { method: "POST" }),
  history: () => request<{ executions: HistoryEntry[] }>("/api/v1/history"),
  getSettings: () => request<Settings>("/api/v1/settings"),
  saveSettings: (body: {
    root?: string;
    privacy?: string;
    model?: string;
    base_url?: string;
    api_key?: string;
  }) => request<Settings>("/api/v1/settings", { method: "PUT", body: JSON.stringify(body) }),
};

export const CAPABILITIES = [
  { icon: "🔍", title: "查找文件", example: "帮我找一下和 Postman 作业有关的文件" },
  { icon: "📂", title: "整理文件", example: "把与安全学习相关的资料整理到 Security-Learning" },
  { icon: "📄", title: "总结内容", example: "在文件库里总结任务书的主要内容" },
  { icon: "✨", title: "其他操作", example: "帮我找出目录里的重复文件" },
];

export function formatSize(n: number): string {
  let size = n;
  for (const unit of ["B", "KB", "MB", "GB"]) {
    if (size < 1024 || unit === "GB") {
      return `${unit === "B" ? size.toFixed(0) : size.toFixed(1)} ${unit}`;
    }
    size /= 1024;
  }
  return `${size.toFixed(1)} GB`;
}

export function formatEpoch(epoch: number | null): string {
  if (!epoch) return "-";
  return new Date(epoch * 1000).toLocaleString("zh-CN", { hour12: false });
}
