import React, { useEffect, useState } from "react";
import { api } from "../api";

// 眼睛图标（睁/闭），控制 API Key 明文显示
const EYE_ON = (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
    <circle cx="12" cy="12" r="3" />
  </svg>
);
const EYE_OFF = (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
    <line x1="1" y1="1" x2="23" y2="23" />
  </svg>
);

// 空白表单
const BLANK = {
  id: null,
  label: "",
  kind: "openai",
  base_url: "",
  api_key: "",
  modelsText: "",
  default_model: "",
  supports_vision: false,
};

// 模型配置中心：左列供应商列表 + 右侧编辑表单。支持增删改、设默认、连通测试。
export default function ModelConfig({ onClose, onChanged }) {
  const [providers, setProviders] = useState([]);
  const [defaultId, setDefaultId] = useState("");
  const [form, setForm] = useState(BLANK);
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState(null); // {ok, text|error}
  const [err, setErr] = useState("");
  const [showKey, setShowKey] = useState(false); // API Key 明文/掩码

  function refresh() {
    return api.providers().then((d) => {
      setProviders(d.providers);
      setDefaultId(d.default_id);
    });
  }

  useEffect(() => {
    refresh();
  }, []);

  function notifyChanged() {
    onChanged && onChanged();
  }

  function newConfig() {
    setForm(BLANK);
    setTest(null);
    setErr("");
  }

  async function pick(pid) {
    setTest(null);
    setErr("");
    try {
      const c = await api.getProvider(pid); // 含 api_key
      setForm({
        id: c.id,
        label: c.label || "",
        kind: c.kind || "openai",
        base_url: c.base_url || "",
        api_key: c.api_key || "",
        modelsText: (c.models || []).join("\n"),
        default_model: c.default_model || "",
        supports_vision: !!c.supports_vision,
      });
    } catch (e) {
      setErr(e.message);
    }
  }

  function payload() {
    const models = form.modelsText
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    return {
      label: form.label.trim() || "未命名",
      kind: form.kind,
      base_url: form.base_url.trim(),
      api_key: form.api_key.trim(),
      models,
      default_model: form.default_model.trim() || models[0] || "",
      supports_vision: form.supports_vision,
    };
  }

  async function save() {
    setBusy(true);
    setErr("");
    try {
      if (form.id) await api.updateProvider(form.id, payload());
      else {
        const c = await api.addProvider(payload());
        await pick(c.id);
      }
      await refresh();
      notifyChanged();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!form.id) return;
    if (!confirm(`确认删除供应商「${form.label}」？`)) return;
    setBusy(true);
    try {
      await api.deleteProvider(form.id);
      newConfig();
      await refresh();
      notifyChanged();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function makeDefault(pid) {
    await api.setDefaultProvider(pid);
    await refresh();
    notifyChanged();
  }

  async function runTest() {
    setBusy(true);
    setTest(null);
    setErr("");
    try {
      const r = await api.testProvider({ config: payload() });
      setTest(r);
    } catch (e) {
      setTest({ ok: false, error: e.message });
    } finally {
      setBusy(false);
    }
  }

  // 「模型列表」文本框解析出的可选模型，供「默认模型」下拉使用
  const modelOptions = form.modelsText
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal mc" onClick={(e) => e.stopPropagation()}>
        <div className="mc-head">
          <h2>模型配置</h2>
          <button className="mc-close" onClick={onClose} title="关闭">✕</button>
        </div>

        <div className="mc-body">
          {/* 左：供应商列表 */}
          <aside className="mc-list">
            {providers.map((p) => (
              <button
                key={p.name}
                className={"mc-item" + (form.id === p.name ? " active" : "")}
                onClick={() => pick(p.name)}
              >
                <span className="mc-item-name">{p.label}</span>
                <span className="mc-item-meta">
                  {p.name === defaultId && <span className="mc-badge def">默认</span>}
                  <span className={"mc-badge " + (p.configured ? "ok" : "off")}>
                    {p.configured ? "已配置" : "未完整"}
                  </span>
                </span>
              </button>
            ))}
            <button className="mc-add" onClick={newConfig}>＋ 新增供应商</button>
          </aside>

          {/* 右：编辑表单 */}
          <section className="mc-form">
            <div className="mc-form-title">
              {form.id ? "编辑供应商" : "新增供应商"}
              {form.id && form.id !== defaultId && (
                <button className="link small" onClick={() => makeDefault(form.id)}>
                  设为默认
                </button>
              )}
              {form.id && form.id === defaultId && (
                <span className="mc-badge def">当前默认</span>
              )}
            </div>

            <label className="mc-field">
              <span>显示名</span>
              <input
                value={form.label}
                onChange={(e) => setForm({ ...form, label: e.target.value })}
                placeholder="如：DeepSeek / 我的中转站"
              />
            </label>

            <label className="mc-field">
              <span>接口协议</span>
              <select
                value={form.kind}
                onChange={(e) => setForm({ ...form, kind: e.target.value })}
              >
                <option value="openai">OpenAI Chat Completions</option>
                <option value="anthropic">Anthropic Messages</option>
              </select>
            </label>

            <label className="mc-field">
              <span>Base URL</span>
              <input
                value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                placeholder={form.kind === "anthropic" ? "官方 Claude 可留空；中转站填地址" : "如 https://api.deepseek.com"}
              />
            </label>

            <label className="mc-field">
              <span>API Key</span>
              <div className="mc-key-wrap">
                <input
                  type={showKey ? "text" : "password"}
                  value={form.api_key}
                  onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                  placeholder="sk-..."
                />
                <button
                  type="button"
                  className="mc-key-eye"
                  onClick={() => setShowKey((v) => !v)}
                  title={showKey ? "隐藏" : "显示"}
                  aria-label={showKey ? "隐藏 API Key" : "显示 API Key"}
                >
                  {showKey ? EYE_OFF : EYE_ON}
                </button>
              </div>
            </label>

            <label className="mc-field">
              <span>模型列表<small className="muted">（每行一个）</small></span>
              <textarea
                rows={3}
                value={form.modelsText}
                onChange={(e) => setForm({ ...form, modelsText: e.target.value })}
                placeholder={"deepseek-v4-flash\ndeepseek-v4-pro"}
              />
            </label>

            <label className="mc-field">
              <span>默认模型</span>
              {modelOptions.length === 0 ? (
                <input disabled placeholder="请先在上方「模型列表」填入至少一个模型" />
              ) : (
                <select
                  value={modelOptions.includes(form.default_model) ? form.default_model : modelOptions[0]}
                  onChange={(e) => setForm({ ...form, default_model: e.target.value })}
                >
                  {modelOptions.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              )}
            </label>

            <label className="mc-check">
              <input
                type="checkbox"
                checked={form.supports_vision}
                onChange={(e) => setForm({ ...form, supports_vision: e.target.checked })}
              />
              <span>支持视觉（可识别图片）</span>
            </label>

            {err && <div className="banner error">{err}</div>}
            {test && (
              <div className={"banner " + (test.ok ? "ok" : "error")}>
                {test.ok ? `连通成功 · ${test.model}：${test.text}` : `连通失败：${test.error}`}
              </div>
            )}

            <div className="mc-actions">
              <button onClick={runTest} disabled={busy}>测试连通</button>
              <span style={{ flex: 1 }} />
              {form.id && (
                <button className="danger" onClick={remove} disabled={busy}>删除</button>
              )}
              <button className="primary" onClick={save} disabled={busy}>
                {busy ? "处理中…" : form.id ? "保存" : "创建"}
              </button>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
