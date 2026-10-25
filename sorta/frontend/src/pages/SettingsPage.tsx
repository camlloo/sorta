import { useEffect, useState } from "react";
import { useWorkspace } from "../App";
import { api, type ConnectionTestResult } from "../services/api";
import TemplateManager from "../components/TemplateManager";

const PRIVACY_MODES = [
  {
    value: "metadata_only",
    title: "仅元数据",
    desc: "只发送文件名与元数据；总结/读取正文一律拒绝",
  },
  {
    value: "ask_before_content",
    title: "逐次确认",
    desc: "读取正文前逐项询问；被拒文件只按文件名判断或列入待确认",
  },
  {
    value: "authorized_content",
    title: "授权读取",
    desc: "授权范围内按需读取限长正文，仍展示当前发送策略",
  },
];

/** 设置：模型配置（兼容 OpenAI）+ 连接测试 + 工作目录与隐私 + 模板。 */

export default function SettingsPage() {
  const workspace = useWorkspace();
  const [root, setRoot] = useState(workspace.root);
  const [privacy, setPrivacy] = useState(workspace.privacy);
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [keyHint, setKeyHint] = useState("");
  const [busy, setBusy] = useState<"" | "save" | "scan" | "test">("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null);

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
  }, [])

  async function save(andScan: boolean) {
    setBusy(andScan ? "scan" : "save");
    setError("");
    setMessage("");
    try {
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
        setMessage(`已保存并索引 ${info.file_count} 个文件。模型配置立即生效，无需重启。`);
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
    <div className="px-8 py-8 pb-16">
      <div className="eyebrow">设置</div>
      <h1 className="mt-2 text-[32px] font-extrabold tracking-tight" style={{ fontWeight: 850 }}>
        模型与授权
      </h1>

      {/* 模型配置 */}
      <section className="mt-8 max-w-2xl">
        <div className="eyebrow">模型 / 兼容 OpenAI 接口</div>
        <div className="mt-3 space-y-4">
          <div>
            <label className="font-mono text-[10px] tracking-wider text-graphite uppercase">
              模型名称
            </label>
            <input
              className="mt-1 w-full border-b border-rule-strong bg-transparent px-1 py-2 font-mono text-[13px] transition-colors duration-100 focus:border-signal focus:outline-none"
              placeholder="qwen3.8-flash / gpt-4o / deepseek-chat"
              value={model}
              onChange={(e) => setModel(e.target.value)}
            />
          </div>
          <div>
            <label className="font-mono text-[10px] tracking-wider text-graphite uppercase">
              Base URL（留空使用官方接口）
            </label>
            <input
              className="mt-1 w-full border-b border-rule-strong bg-transparent px-1 py-2 font-mono text-[13px] transition-colors duration-100 focus:border-signal focus:outline-none"
              placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
            />
          </div>
          <div>
            <label className="font-mono text-[10px] tracking-wider text-graphite uppercase">
              API Key
            </label>
            <input
              type="password"
              className="mt-1 w-full border-b border-rule-strong bg-transparent px-1 py-2 font-mono text-[13px] transition-colors duration-100 focus:border-signal focus:outline-none"
              placeholder={keyHint ? `已配置（${keyHint}），留空保持不变` : "sk-…"}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
            <p className="mt-1 font-mono text-[10px] text-graphite">
              仅存本机数据目录，不回显、不上传；留空保持现有密钥。
            </p>
          </div>
        </div>

        <div className="mt-4 flex items-center gap-3">
          <button
            className="border border-rule px-3 py-2 font-mono text-[11px] transition-colors duration-100 hover:border-ink hover:bg-ink hover:text-paper disabled:opacity-40"
            disabled={busy !== "" || model.trim() === ""}
            onClick={async () => {
              setBusy("test");
              setError("");
              try {
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
            {busy === "test" ? "测试中…" : "测试连接"}
          </button>
          <span className="font-mono text-[10px] text-graphite">
            最小测试请求，不含你的文件；可能产生少量调用费用。
          </span>
        </div>

        {testResult && (
          <div className="mt-3 border-t border-rule pt-3 font-mono text-[11px] leading-relaxed">
            <div
              className={`font-semibold ${
                testResult.ok ? "text-verify" : "text-signal"
              }`}
            >
              {testResult.ok
                ? "■ 连接正常，规划功能可用"
                : testResult.checks.chat.ok
                  ? "□ 对话正常，但规划不可用（工具调用不兼容）"
                  : "× 连接失败"}
              <span className="ml-2 font-normal text-graphite">{testResult.model}</span>
            </div>
            <div className={testResult.checks.chat.ok ? "text-graphite" : "text-signal"}>
              chat — {testResult.checks.chat.message} [{testResult.checks.chat.latency_ms}ms]
            </div>
            <div className={testResult.checks.tools.ok ? "text-graphite" : "text-signal"}>
              tools — {testResult.checks.tools.message} [{testResult.checks.tools.latency_ms}ms]
              {!testResult.checks.tools.ok && " · 该端点无法用于自动整理"}
            </div>
          </div>
        )}
      </section>

      {/* 工作目录 */}
      <section className="mt-10 max-w-2xl">
        <div className="eyebrow">工作目录 / 授权范围</div>
        <input
          className="mt-3 w-full border-b border-rule-strong bg-transparent px-1 py-2 font-mono text-[13px] transition-colors duration-100 focus:border-signal focus:outline-none"
          placeholder="D:\Downloads"
          value={root}
          onChange={(e) => setRoot(e.target.value)}
        />
        <p className="mt-1 font-mono text-[10px] text-graphite">
          所有扫描与文件操作仅限该目录内；执行前必须经你确认。
        </p>

        <div className="mt-4 border-t border-rule">
          {PRIVACY_MODES.map((mode) => (
            <label
              key={mode.value}
              className={`flex cursor-pointer items-start gap-3 border-b border-rule px-2 py-3 transition-colors duration-100 ${
                privacy === mode.value ? "bg-ink text-paper" : "hover:bg-ink/5"
              }`}
            >
              <input
                type="radio"
                name="privacy"
                className="mt-1"
                checked={privacy === mode.value}
                onChange={() => setPrivacy(mode.value)}
              />
              <span>
                <span className="block text-[13px] font-semibold">{mode.title}</span>
                <span
                  className={`block font-mono text-[10px] ${
                    privacy === mode.value ? "text-paper/70" : "text-graphite"
                  }`}
                >
                  {mode.desc}
                </span>
              </span>
            </label>
          ))}
        </div>

        <div className="mt-4 flex gap-2">
          <button
            className="border border-rule px-4 py-2 text-[13px] font-medium transition-colors duration-100 hover:border-ink hover:bg-ink hover:text-paper disabled:opacity-40"
            disabled={busy !== ""}
            onClick={() => save(false)}
          >
            {busy === "save" ? "保存中…" : "保存设置"}
          </button>
          <button
            className="bg-ink px-4 py-2 text-[13px] font-semibold text-paper transition-colors duration-100 hover:bg-signal disabled:opacity-40"
            disabled={busy !== "" || root.trim() === ""}
            onClick={() => save(true)}
          >
            {busy === "scan" ? "扫描中…" : "保存并扫描"}
          </button>
        </div>
      </section>

      {/* 模板 */}
      <section className="mt-10 max-w-2xl">
        <TemplateManager />
      </section>

      <p className="mt-10 max-w-2xl border-t border-rule pt-3 font-mono text-[10px] leading-relaxed text-graphite">
        扫描会话有效期 1 小时 · 数据保存在本机 SQLite，不上传 ·
        模型配置修改即时生效 · 所有文件操作先计划后执行、可撤销
      </p>

      {message && (
        <div className="mt-4 max-w-2xl border-l-2 border-verify pl-3 font-mono text-[11px] text-verify">
          {message}
        </div>
      )}
      {error && (
        <div className="mt-4 max-w-2xl border-l-2 border-signal pl-3 font-mono text-[11px] text-signal">
          {error}
        </div>
      )}
    </div>
  );
}
