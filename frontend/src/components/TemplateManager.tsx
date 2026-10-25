import { useCallback, useEffect, useState } from "react";
import { api, type Template } from "../services/api";

/** 整理模板管理：表格式（增 / 改 / 停用 / 删）。EXT-01。 */

export default function TemplateManager() {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [newName, setNewName] = useState("");
  const [newInstruction, setNewInstruction] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const data = await api.listTemplates();
      setTemplates(data.templates);
      setLoaded(true);
      setError("");
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  useEffect(() => {
    let active = true;
    api.listTemplates()
      .then((data) => {
        if (active) { setTemplates(data.templates); setLoaded(true); }
      })
      .catch((err: Error) => { if (active) setError(err.message); });
    return () => { active = false; };
  }, []);

  async function add() {
    if (!newName.trim() || !newInstruction.trim()) return;
    setBusy(true);
    setError("");
    try {
      await api.addTemplate(newName.trim(), newInstruction.trim());
      setNewName("");
      setNewInstruction("");
      setMessage("模板已添加。");
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function toggle(t: Template) {
    setBusy(true);
    try {
      await api.updateTemplate(t.id, { enabled: !t.enabled });
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(t: Template) {
    if (!window.confirm(`删除模板「${t.name}」？此操作不可恢复。`)) return;
    setBusy(true);
    try {
      await api.deleteTemplate(t.id);
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function saveEdit(t: Template) {
    setBusy(true);
    try {
      await api.updateTemplate(t.id, { instruction: editDraft });
      setEditingId(null);
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="eyebrow">整理模板 / 可复用偏好</div>
      <p className="mt-1 font-mono text-[10px] text-graphite">
        不授予写权限；与指令冲突时以指令为准
      </p>

      {loaded && templates.length === 0 && (
        <p className="mt-3 font-mono text-[11px] text-graphite">暂无模板。</p>
      )}
      <div className="mt-3 border-t border-ink">
        {templates.map((t) => (
          <div key={t.id} className="border-b border-rule px-2 py-3">
            <div className="flex items-baseline justify-between gap-3">
              <div className="flex items-baseline gap-2">
                <span className={`text-[13px] font-semibold ${t.enabled ? "" : "text-graphite line-through"}`}>
                  {t.name}
                </span>
                {t.builtin && (
                  <span className="border border-current px-1 font-mono text-[9px] text-graphite">
                    内置
                  </span>
                )}
              </div>
              <div className="flex shrink-0 items-center gap-3 font-mono text-[10px]">
                <label className="flex cursor-pointer items-center gap-1 text-graphite">
                  <input
                    type="checkbox"
                    checked={t.enabled}
                    disabled={busy}
                    onChange={() => toggle(t)}
                  />
                  启用
                </label>
                <button
                  className="underline-offset-2 hover:text-ink hover:underline"
                  onClick={() => {
                    setEditingId(t.id);
                    setEditDraft(t.instruction);
                  }}
                >
                  编辑
                </button>
                <button
                  className="text-signal underline-offset-2 hover:underline disabled:opacity-40"
                  disabled={busy}
                  onClick={() => remove(t)}
                >
                  删除
                </button>
              </div>
            </div>
            {editingId === t.id ? (
              <div className="mt-2">
                <textarea
                  className="w-full border-b border-rule-strong bg-transparent px-1 py-1.5 font-mono text-[11px] leading-relaxed focus:border-signal focus:outline-none"
                  rows={3}
                  value={editDraft}
                  onChange={(e) => setEditDraft(e.target.value)}
                />
                <div className="mt-1 flex gap-2">
                  <button
                    className="bg-ink px-3 py-1 font-mono text-[10px] text-paper disabled:opacity-40"
                    disabled={busy}
                    onClick={() => saveEdit(t)}
                  >
                    保存
                  </button>
                  <button
                    className="border border-rule px-3 py-1 font-mono text-[10px]"
                    onClick={() => setEditingId(null)}
                  >
                    取消
                  </button>
                </div>
              </div>
            ) : (
              <p className="mt-1 font-mono text-[11px] leading-relaxed text-graphite">
                {t.instruction}
              </p>
            )}
          </div>
        ))}
      </div>

      <div className="mt-4">
        <div className="font-mono text-[10px] tracking-wider text-graphite uppercase">新增模板</div>
        <input
          className="mt-2 w-full border-b border-rule-strong bg-transparent px-1 py-2 text-[12px] transition-colors duration-100 focus:border-signal focus:outline-none"
          placeholder="模板名称，例如：下载目录清理"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
        <textarea
          className="mt-2 w-full border-b border-rule-strong bg-transparent px-1 py-2 font-mono text-[11px] leading-relaxed transition-colors duration-100 focus:border-signal focus:outline-none"
          rows={3}
          placeholder="整理偏好：目标目录结构、排除规则、move/copy 策略…"
          value={newInstruction}
          onChange={(e) => setNewInstruction(e.target.value)}
        />
        <button
          className="mt-2 border border-ink px-3 py-1.5 font-mono text-[11px] transition-colors duration-100 hover:bg-ink hover:text-paper disabled:opacity-40"
          disabled={busy || !newName.trim() || !newInstruction.trim()}
          onClick={add}
        >
          + 添加模板
        </button>
      </div>

      {error && (
        <p className="mt-2 border-l-2 border-signal pl-2 font-mono text-[10px] text-signal">
          {error}
        </p>
      )}
      {message && (
        <p className="mt-2 border-l-2 border-verify pl-2 font-mono text-[10px] text-verify">
          {message}
        </p>
      )}
    </div>
  );
}
