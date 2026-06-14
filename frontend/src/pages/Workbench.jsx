import React, { useEffect, useRef, useState } from "react";
import { api } from "../api";
import OutputView from "../components/OutputView.jsx";

/**
 * 会话工作台。结构：
 *   ① 输入素材
 *   ② 补充背景
 *   ③ 产出区：左侧 Tab 条 + 右侧主面板
 *
 * 每个产出独立选模型、独立生成、独立看内容。
 * 依赖未满足时 Tab 会变灰提示，按钮也会禁用。
 */
export default function Workbench({ module, sessionId, onBack }) {
  const [session, setSession] = useState(null);
  const [providers, setProviders] = useState([]);
  const [prePrompt, setPrePrompt] = useState("");
  const [savedHint, setSavedHint] = useState("");
  const [uploading, setUploading] = useState(false);
  const [activeKey, setActiveKey] = useState(module.steps[0]?.key);

  const [artifacts, setArtifacts] = useState({}); // step.key → content
  const [stepStatus, setStepStatus] = useState({}); // step.key → {running, percent, message, err}

  const fileRef = useRef(null);
  const esRefs = useRef({});

  const stepModels = session?.step_models || {};
  const activeStep = module.steps.find((s) => s.key === activeKey) || module.steps[0];

  function load() {
    api.getSession(sessionId).then((s) => {
      setSession(s);
      setPrePrompt(s.pre_prompt || "");
      module.steps.forEach((step) => {
        if ((s.artifacts || []).includes(step.output_name)) {
          api.getArtifact(sessionId, step.output_name).then((a) =>
            setArtifacts((m) => ({ ...m, [step.key]: a.content }))
          );
        }
      });
    });
  }

  // 首次加载：会话 + provider 列表 + 各步骤进度（恢复 UI）
  useEffect(() => {
    api.providers().then((d) => setProviders(d.providers));
    load();
    api.getProgress(sessionId).then((p) => {
      const newStatus = {};
      module.steps.forEach((step) => {
        const evts = p.by_step?.[step.key] || [];
        if (evts.length === 0) return;
        const last = evts[evts.length - 1];
        const isRunning = (p.running || []).includes(step.key);
        newStatus[step.key] = {
          running: isRunning,
          percent: typeof last.percent === "number" ? last.percent : (last.type === "done" ? 100 : 0),
          message: last.message || "",
          err: last.type === "error" ? last.message : "",
        };
        if (isRunning) attachStream(step.key);
      });
      setStepStatus(newStatus);
    });
    // 离开时清理所有 EventSource
    return () => {
      Object.values(esRefs.current).forEach((es) => es?.close());
      esRefs.current = {};
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  async function savePrePrompt() {
    await api.updateSession(sessionId, { pre_prompt: prePrompt });
    setSavedHint("已保存");
    setTimeout(() => setSavedHint(""), 1800);
  }

  async function onFiles(e) {
    const files = e.target.files;
    if (!files?.length) return;
    setUploading(true);
    try {
      await api.uploadInputs(sessionId, files);
      load();
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function onDeleteFile(name) {
    if (!confirm(`删除文件「${name}」？`)) return;
    try {
      await api.deleteInput(sessionId, name);
      load();
    } catch (e) {
      alert("删除失败：" + e.message);
    }
  }

  async function onRenameFile(name) {
    const dot = name.lastIndexOf(".");
    const stem = dot > 0 ? name.slice(0, dot) : name;
    const ext = dot > 0 ? name.slice(dot) : "";
    const next = prompt("重命名（保留扩展名 " + (ext || "无") + "）", stem);
    if (next == null) return;
    const trimmed = next.trim();
    if (!trimmed || trimmed === stem) return;
    try {
      await api.renameInput(sessionId, name, trimmed + ext);
      load();
    } catch (e) {
      alert("重命名失败：" + e.message);
    }
  }

  function setStatus(key, patch) {
    setStepStatus((m) => ({ ...m, [key]: { ...(m[key] || {}), ...patch } }));
  }

  function attachStream(stepKey) {
    if (esRefs.current[stepKey]) return; // 已订阅
    const es = api.stepStream(sessionId, stepKey);
    esRefs.current[stepKey] = es;
    setStatus(stepKey, { running: true, err: "" });
    es.onmessage = (e) => {
      let evt;
      try { evt = JSON.parse(e.data); } catch { return; }
      if (evt.type === "ping") return;
      if (evt.type === "step" || evt.type === "start") {
        setStatus(stepKey, {
          running: true,
          percent: typeof evt.percent === "number" ? evt.percent : undefined,
          message: evt.message || "",
        });
      } else if (evt.type === "artifact") {
        api.getArtifact(sessionId, evt.name).then((a) => {
          setArtifacts((m) => ({ ...m, [stepKey]: a.content }));
          load();
        });
      } else if (evt.type === "done") {
        setStatus(stepKey, { running: false, percent: 100, message: "完成" });
        finishStream(stepKey);
      } else if (evt.type === "error") {
        setStatus(stepKey, { running: false, err: evt.message || "生成失败" });
        finishStream(stepKey);
      }
    };
  }

  function finishStream(stepKey) {
    esRefs.current[stepKey]?.close();
    delete esRefs.current[stepKey];
  }

  async function generate(stepKey) {
    setStatus(stepKey, { running: true, percent: 0, message: "启动后台任务…", err: "" });
    try {
      await api.startStep(sessionId, stepKey);
    } catch (e) {
      setStatus(stepKey, { running: false, err: e.message });
      return;
    }
    attachStream(stepKey);
  }

  async function changeStepModel(stepKey, provider, model) {
    await api.setStepModel(sessionId, { step: stepKey, provider, model });
    setSession((s) => ({
      ...s,
      step_models: { ...(s.step_models || {}), [stepKey]: { provider, model } },
    }));
  }

  function checkDeps(step) {
    if (!hasInputs) return { ok: false, hint: "请先上传输入素材" };
    for (const req of step.requires || []) {
      if (!artifacts[req]) {
        const reqDef = module.steps.find((s) => s.key === req);
        return { ok: false, hint: `需先生成「${reqDef?.title || req}」` };
      }
    }
    if ((step.requires_any || []).length > 0) {
      const anyOk = step.requires_any.some((req) => !!artifacts[req]);
      if (!anyOk) {
        const titles = step.requires_any.map(
          (req) => module.steps.find((s) => s.key === req)?.title || req
        );
        return { ok: false, hint: `需先生成「${titles.join("」或「")}」之一` };
      }
    }
    return { ok: true, hint: "" };
  }

  if (!session) return <div className="empty">加载项目…</div>;
  const hasInputs = session.inputs?.length > 0;
  const activeDep = checkDeps(activeStep);
  const activeStatus = stepStatus[activeStep.key] || {};
  const activeContent = artifacts[activeStep.key];
  const activeModel = stepModels[activeStep.key] || {};
  const defaultProvider = providers.find((p) => p.is_default);
  const defaultModelName = defaultProvider?.default_model || "";
  const activeProviderName = activeModel.provider || defaultProvider?.name || "";
  const activeProvider = providers.find((p) => p.name === activeProviderName);
  const activeModelName = activeModel.model || activeProvider?.default_model || "";

  return (
    <div className="workbench">
      <div className="wb-head">
        <button className="link" onClick={onBack}>← 返回</button>
        <h1>{session.title}</h1>
        <span className="muted small">状态：{session.status}</span>
      </div>

      {/* ① 输入素材 */}
      <section className="card">
        <h2>① 输入素材</h2>
        <p className="muted small">
          转写文字稿（txt，主体）；辅助资料（pdf）；课堂手写笔记（图片）。
        </p>
        <div className="upload-row">
          <input
            ref={fileRef}
            id="wb-file-input"
            type="file"
            multiple
            accept=".txt,.md,.pdf,.png,.jpg,.jpeg"
            onChange={onFiles}
            className="hidden-file"
          />
          <label htmlFor="wb-file-input" className="upload-btn">
            <span className="upload-ico">＋</span>
            <span>{uploading ? "上传中…" : "选择文件"}</span>
          </label>
          <span className="muted small">支持 txt / md / pdf / png / jpg；可多选</span>
        </div>
        <div className="file-list">
          {hasInputs ? (
            session.inputs.map((f) => (
              <div className="file-row" key={f}>
                <span className="file-name" title={f}>{f}</span>
                <div className="file-actions">
                  <button className="file-act" onClick={() => onRenameFile(f)} title="重命名">改名</button>
                  <button className="file-act danger" onClick={() => onDeleteFile(f)} title="删除">删除</button>
                </div>
              </div>
            ))
          ) : (
            <span className="muted small">尚未上传素材。</span>
          )}
        </div>
      </section>

      {/* ② 补充背景 */}
      <section className="card">
        <h2>② 补充背景 &amp; 重点要求</h2>
        <p className="muted small">
          可选。这里填的内容会注入所有产出的提示词，建议先填好再生成。
        </p>
        <textarea
          rows={4}
          value={prePrompt}
          onChange={(e) => setPrePrompt(e.target.value)}
          placeholder="如：老师叫张三；公司案例是宁德时代；重点体现 DCF 与可比公司法的对比；术语 WACC 必须保留英文。"
        />
        <div className="row-actions">
          <button className="primary ghost" onClick={savePrePrompt}>保存背景</button>
          {savedHint && <span className="muted small saved">{savedHint}</span>}
        </div>
      </section>

      {/* ③ 产出区：横版 Tab + 下方面板 */}
      <section className="products-h">
        <h2 style={{ margin: "0 0 14px" }}>③ 产出</h2>
        <div className="products-h-body">
          {/* 上：横向 Tab 条 */}
          <nav className="products-tabs">
            {module.steps.map((step) => {
              const dep = checkDeps(step);
              const has = !!artifacts[step.key];
              const st = stepStatus[step.key] || {};
              const sm = stepModels[step.key];
              const subText = st.running
                ? `${st.percent || 0}% · 生成中`
                : sm?.model || defaultModelName || "—";
              return (
                <button
                  key={step.key}
                  className={
                    "products-tab" +
                    (step.key === activeKey ? " active" : "") +
                    (!dep.ok ? " disabled" : "") +
                    (has ? " has-output" : "")
                  }
                  onClick={() => setActiveKey(step.key)}
                >
                  <div className="ptab-row">
                    <span className="ptab-name">{step.title}</span>
                    {has && <span className="ptab-dot ok">●</span>}
                    {st.running && <span className="ptab-dot running">●</span>}
                  </div>
                  <div className="ptab-sub muted">{subText}</div>
                </button>
              );
            })}
          </nav>

          {/* 下：主面板 */}
          <div className="products-panel card">
            <div className="panel-head">
              <div>
                <h3 className="panel-title">{activeStep.title}</h3>
                <p className="muted small panel-desc">{activeStep.description}</p>
              </div>
              <div className="panel-actions">
                <select
                  className="model-select"
                  value={activeProviderName}
                  onChange={(e) => {
                    const provName = e.target.value;
                    const prov = providers.find((p) => p.name === provName);
                    changeStepModel(activeStep.key, provName, prov?.default_model || "");
                  }}
                  disabled={activeStatus.running}
                  title="选择 Provider"
                >
                  {providers.filter((p) => p.configured).map((p) => (
                    <option key={p.name} value={p.name}>{p.label || p.name}</option>
                  ))}
                </select>
                <select
                  className="model-select model-select-narrow"
                  value={activeModelName}
                  onChange={(e) => changeStepModel(activeStep.key, activeProviderName, e.target.value)}
                  disabled={activeStatus.running || !activeProvider}
                >
                  {(activeProvider?.models || []).map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                  {activeModelName && !(activeProvider?.models || []).includes(activeModelName) && (
                    <option key={activeModelName} value={activeModelName}>{activeModelName}</option>
                  )}
                </select>
                <button
                  className="primary"
                  disabled={!activeDep.ok || activeStatus.running}
                  title={activeDep.ok ? "" : activeDep.hint}
                  onClick={() => generate(activeStep.key)}
                >
                  {activeStatus.running
                    ? "生成中…"
                    : activeContent ? "重新生成" : "生成"}
                </button>
              </div>
            </div>

            {!activeDep.ok && (
              <div className="dep-hint">⛔ {activeDep.hint}</div>
            )}

            {(activeStatus.running || (activeStatus.percent && !activeContent)) && !activeStatus.err && (
              <div className="progress-card inline">
                <div className="progress-track">
                  <div className="progress-fill" style={{ width: (activeStatus.percent || 0) + "%" }} />
                </div>
                <div className="muted small">
                  {activeStatus.percent || 0}% · {activeStatus.message || "处理中…"}
                </div>
              </div>
            )}
            {activeStatus.err && <div className="banner error">{activeStatus.err}</div>}

            <div className="panel-content">
              <OutputView name={activeStep.output_name} content={activeContent} />
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
