import { useCallback, useEffect, useState } from "react";
import { api, type Template } from "../services/api";

/** EXT-01：整理模板管理——查看、新增、编辑、停用/启用、删除。 */

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
    void refresh();
  }, [refresh]);

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
    <div className="mt-8 max-w-3xl rounded border border-neutral-200 p-4">
      <h2 className="text-sm font-bold">整理模板</h2>
      <p className="mt-1 text-xs text-neutral-500">
        可复用的整理偏好（目标结构、排除规则、策略约定）。模板只影响生成的计划，
        不能授予写权限；每次仍生成可审阅计划，与当前指令冲突时以指令为准。
      </p>

      {loaded && templates.length === 0 && (
        <p className="mt-2 text-xs text-neutral-400">暂无模板。</p>
      )}
      <div className="mt-3 space-y-3">
        {templates.map((t) => (
          <div key={t.id} className="rounded border border-neutral-100 p-3">
            <div className="flex items-center justify-between">
              <div className="text-sm font-semibold">
                {t.name}
                {t.builtin && (
                  <span className="ml-2 rounded bg-neutral-100 px-1.5 py-0.5 text-[10px] text-neutral-500">
                    内置
                  </span>
                )}
              </div>
              <label className="flex items-center gap-1 text-xs text-neutral-500">
                <input
                  type="checkbox"
                  checked={t.enabled}
                  disabled={busy}
                  onChange={() => toggle(t)}
                />
                启用
              </label>
            </div>
            {editingId === t.id ? (
              <div className="mt-2">
                <textarea
                  className="w-full rounded border border-neutral-300 p-2 text-xs"
                  rows={3}
                  value={editDraft}
                  onChange={(e) => setEditDraft(e.target.value)}
                />
                <div className="mt-1 flex gap-2">
                  <button
                    className="rounded bg-neutral-900 px-2 py-1 text-xs text-white"
                    disabled={busy}
                    onClick={() => saveEdit(t)}
                  >
                    保存
                  </button>
                  <button
                    className="rounded border border-neutral-300 px-2 py-1 text-xs"
                    onClick={() => setEditingId(null)}
                  >
                    取消
                  </button>
                </div>
              </div>
            ) : (
              <>
                <p className="mt-1 whitespace-pre-wrap text-xs text-neutral-600">
                  {t.instruction}
                </p>
                <div className="mt-1 flex gap-2">
                  <button
                    className="text-xs text-blue-600 hover:underline"
                    onClick={() => {
                      setEditingId(t.id);
                      setEditDraft(t.instruction);
                    }}
                  >
                    编辑
                  </button>
                  <button
                    className="text-xs text-red-600 hover:underline"
                    disabled={busy}
                    onClick={() => remove(t)}
                  >
                    删除
                  </button>
                </div>
              </>
            )}
          </div>
        ))}
      </div>

      <div className="mt-4 border-t border-neutral-100 pt-3">
        <div className="text-xs font-semibold text-neutral-700">新增模板</div>
        <input
          className="mt-2 w-full rounded border border-neutral-300 px-2 py-1.5 text-xs"
          placeholder="模板名称，例如：下载目录清理"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
        <textarea
          className="mt-2 w-full rounded border border-neutral-300 p-2 text-xs"
          rows={3}
          placeholder="整理偏好：目标目录结构、排除规则、move/copy 策略……"
          value={newInstruction}
          onChange={(e) => setNewInstruction(e.target.value)}
        />
        <button
          className="mt-2 rounded bg-neutral-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
          disabled={busy || !newName.trim() || !newInstruction.trim()}
          onClick={add}
        >
          添加模板
        </button>
      </div>

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
      {message && <p className="mt-2 text-xs text-green-700">{message}</p>}
    </div>
  );
}
