import { useEffect, useState } from "react";
import { useWorkspace } from "../App";
import { api, type ConnectionTestResult } from "../services/api";
import TemplateManager from "../components/TemplateManager";

/** 示意图 ③ 设置页：工作目录授权 + 隐私模式 + LLM 模型配置（兼容 OpenAI 接口）。 */

export default function SettingsPage() {
  const workspace = useWorkspace();
  const [root, setRoot] = useState(workspace.root);
  const [privacy, setPrivacy] = useState(workspace.privacy);
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [keyHint, setKeyHint] = useState("");
  const [busy, setBusy] = useState<"" | "save" | "scan" | "test">("");
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .getSettings()
      .then((s) => {
        setRoot((r) => r || s.root);
        setPrivacy(s.privacy);
        setModel(s.model);
        setBaseUrl(s.base_url);
        setKeyHint(s.api_key_hint ?? "");
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  async function save(andScan: boolean) {
    setBusy(andScan ? "scan" : "save");
    setError("");
    setMessage("");
    try {
      // 模型设置 + 目录/隐私一起保存；API Key 仅在用户输入时提交（不回显）
      const settings = await api.saveSettings({
        root,
        privacy,
        model,
        base_url: baseUrl,
        api_key: apiKey || undefined,
      });
      workspace.refreshSettings();
      setApiKey("");
      setKeyHint(settings.api_key_hint ?? "");
      if (andScan) {
        const info = await api.createWorkspace(root);
        workspace.setWorkspace(info);
        setMessage(
          `已保存设置并索引 ${info.file_count} 个文件。模型配置立即生效，无需重启。`,
        );
      } else {
        setMessage("设置已保存。模型配置立即生效，无需重启。");
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="h-full overflow-y-auto px-8 py-6">
      <h1 className="text-xl font-bold">设置</h1>

      <h2 className="mt-6 text-sm font-bold">模型配置（兼容 OpenAI 接口）</h2>
      <div className="mt-2 max-w-xl space-y-3">
        <div>
          <label className="block text-xs text-neutral-500">模型名称</label>
          <input
            className="mt-1 w-full rounded border border-neutral-300 px-3 py-2 font-mono text-sm focus:border-neutral-500 focus:outline-none"
            placeholder="例如 qwen3.8-flash、gpt-4o、deepseek-chat"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          />
        </div>
        <div>
          <label className="block text-xs text-neutral-500">
            API Base URL（OpenAI 兼容端点；留空使用官方接口）
          </label>
          <input
            className="mt-1 w-full rounded border border-neutral-300 px-3 py-2 font-mono text-sm focus:border-neutral-500 focus:outline-none"
            placeholder="例如 https://dashscope.aliyuncs.com/compatible-mode/v1"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
        </div>
        <div>
          <label className="block text-xs text-neutral-500">API Key</label>
          <input
            type="password"
            className="mt-1 w-full rounded border border-neutral-300 px-3 py-2 font-mono text-sm focus:border-neutral-500 focus:outline-none"
            placeholder={keyHint ? `已配置（${keyHint}），留空保持不变` : "sk-..."}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
          <p className="mt-1 text-xs text-neutral-400">
            密钥仅保存在本机数据目录，不会回显、不会上传；留空表示保持现有密钥。
          </p>
        </div>
      </div>

      <div className="mt-4 flex items-center gap-2">
        <button
          className="rounded border border-neutral-300 px-4 py-2 text-sm disabled:opacity-50"
          disabled={busy !== "" || model.trim() === "" || apiKey === "" && keyHint === ""}
          onClick={async () => {
            setBusy("test");
            setError("");
            try {
              // 先保存再测试：测试始终使用当前已保存的配置
              await api.saveSettings({
                model,
                base_url: baseUrl,
                api_key: apiKey || undefined,
              });
              workspace.refreshSettings();
              setTestResult(await api.testConnection());
            } catch (err) {
              setError((err as Error).message);
            } finally {
              setBusy("");
            }
          }}
        >
          {busy === "test" ? "测试中…" : "🔌 测试连接"}
        </button>
        <span className="text-xs text-neutral-400">
          只发送最小测试请求（不包含你的文件），可能产生少量模型调用费用。
        </span>
      </div>
      {testResult && (
        <div className="mt-3 max-w-xl space-y-1 rounded border border-neutral-200 p-3 text-xs">
          <div className={testResult.ok ? "font-semibold text-green-700" : "font-semibold text-red-700"}>
            {testResult.ok
              ? "✓ 连接正常，规划功能可用"
              : testResult.checks.chat.ok
                ? "⚠ 连接正常，但规划功能不可用（工具调用不兼容）"
                : "✗ 连接失败"}
            <span className="ml-1 text-neutral-400">模型 {testResult.model}</span>
          </div>
          <div className={testResult.checks.chat.ok ? "text-neutral-600" : "text-red-600"}>
            对话：{testResult.checks.chat.message}（{testResult.checks.chat.latency_ms} ms）
            {testResult.checks.chat.code && ` [${testResult.checks.chat.code}]`}
          </div>
          <div className={testResult.checks.tools.ok ? "text-neutral-600" : "text-red-600"}>
            工具调用：{testResult.checks.tools.message}（{testResult.checks.tools.latency_ms} ms）
            {testResult.checks.tools.code && ` [${testResult.checks.tools.code}]`}
            {!testResult.checks.tools.ok && " —— 该端点无法用于自动整理，请更换兼容 OpenAI 工具调用的服务"}
          </div>
        </div>
      )}

      <h2 className="mt-8 text-sm font-bold">工作目录</h2>
      <label className="mt-2 block text-xs text-neutral-500">授权根目录</label>
      <input
        className="mt-1 w-full max-w-xl rounded border border-neutral-300 px-3 py-2 text-sm focus:border-neutral-500 focus:outline-none"
        placeholder="例如 D:\Downloads"
        value={root}
        onChange={(e) => setRoot(e.target.value)}
      />
      <div className="mt-4">
        <div className="text-xs text-neutral-500">隐私模式（发送给云端模型的内容）</div>
        {[
          { value: "metadata_only", label: "仅元数据（不发送文件内容）" },
          {
            value: "ask_before_content",
            label: "逐次确认（读取正文前逐项询问；未授权的文件只按文件名判断）",
          },
          { value: "authorized_content", label: "授权读取内容（文件文本将发送给云端模型）" },
        ].map((option) => (
          <label key={option.value} className="mt-2 flex items-center gap-2 text-sm">
            <input
              type="radio"
              name="privacy"
              checked={privacy === option.value}
              onChange={() => setPrivacy(option.value)}
            />
            {option.label}
          </label>
        ))}
        <p className="mt-2 text-xs text-neutral-400">
          metadata_only：只发送文件名与元数据；ask_before_content：规划中读取被拒的
          文件将列入待确认，文件总结会在界面上向你请求单次授权；authorized_content：
          发送限长文本提取。所有文件操作仅限该目录内，执行前必须经你确认。
        </p>
      </div>

      <div className="mt-4 flex gap-2">
        <button
          className="rounded border border-neutral-300 px-4 py-2 text-sm font-medium disabled:opacity-50"
          disabled={busy !== ""}
          onClick={() => save(false)}
        >
          {busy === "save" ? "保存中…" : "保存设置"}
        </button>
        <button
          className="rounded bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          disabled={busy !== "" || root.trim() === ""}
          onClick={() => save(true)}
        >
          {busy === "scan" ? "扫描中…" : "保存并扫描"}
        </button>
      </div>

      <TemplateManager />

      <h2 className="mt-8 text-sm font-bold">关于</h2>
      <p className="mt-2 max-w-xl text-xs text-neutral-400">
        扫描会话有效期 1 小时；所有文件操作仅限授权目录内，执行前必须经你确认；
        数据保存在本机 SQLite，不上传。模型配置保存后立即生效，无需重启。
      </p>

      {message && (
        <div className="mt-4 max-w-xl rounded bg-neutral-100 px-3 py-2 text-xs">{message}</div>
      )}
      {error && (
        <div className="mt-4 max-w-xl rounded bg-red-50 px-3 py-2 text-xs text-red-700">
          {error}
        </div>
      )}
    </div>
  );
}
