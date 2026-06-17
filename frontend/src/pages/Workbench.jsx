import React, { useEffect, useRef, useState } from "react";
import { api } from "../api";
import OutputView from "../components/OutputView.jsx";
import InlineEdit from "../components/InlineEdit.jsx";

const IMAGE_EXTS = [".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"];
const isImageFile = (name) =>
  IMAGE_EXTS.some((ext) => name.toLowerCase().endsWith(ext));

/**
 * 会话工作台。结构：
 *   ① 输入素材
 *   ② 补充背景
 *   ③ 产出区：左侧 Tab 条 + 右侧主面板
 *
 * 每个产出独立选模型、独立生成、独立看内容。
 * 依赖未满足时 Tab 会变灰提示，按钮也会禁用。
 */
export default function Workbench({ module, sessionId, onBack, providersVersion = 0 }) {
  const [session, setSession] = useState(null);
  const [providers, setProviders] = useState([]);
  const [prePrompt, setPrePrompt] = useState("");
  const [savedHint, setSavedHint] = useState("");
  const [uploading, setUploading] = useState(false);
  const [activeKey, setActiveKey] = useState(module.steps[0]?.key);

  const [artifacts, setArtifacts] = useState({}); // step.key → content
  const [stepStatus, setStepStatus] = useState({}); // step.key → {running, percent, message, err}
  const [editingTitle, setEditingTitle] = useState(false); // 是否在改项目名
  const [editingFile, setEditingFile] = useState(null); // 正在改名的文件原名（null=无）
  const [versions, setVersions] = useState({}); // step.key → [{version, note}]
  const [viewVersion, setViewVersion] = useState({}); // step.key → 0(当前) | 历史版本号
  const [viewContent, setViewContent] = useState({}); // step.key → 历史版本内容
  const [reviseText, setReviseText] = useState({}); // step.key → 修订意见输入
  const [exportHint, setExportHint] = useState("");

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
          api.getArtifact(sessionId, step.output_name).then((a) => {
            setArtifacts((m) => ({ ...m, [step.key]: a.content }));
            setVersions((m) => ({ ...m, [step.key]: a.versions || [] }));
          });
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

  // 模型配置面板改动后，重新拉取 provider 列表（不打断进行中的生成）
  useEffect(() => {
    if (providersVersion > 0) api.providers().then((d) => setProviders(d.providers));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providersVersion]);

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

  // 提交文件改名：value 是不含扩展名的新主名（扩展名自动保留）
  async function commitRenameFile(name, value) {
    const dot = name.lastIndexOf(".");
    const stem = dot > 0 ? name.slice(0, dot) : name;
    const ext = dot > 0 ? name.slice(dot) : "";
    const trimmed = value.trim();
    setEditingFile(null);
    if (!trimmed || trimmed === stem) return;
    try {
      await api.renameInput(sessionId, name, trimmed + ext);
      load();
    } catch (e) {
      alert("重命名失败：" + e.message);
    }
  }

  async function commitRenameSession(value) {
    const name = value.trim();
    setEditingTitle(false);
    if (!name || name === session.title) return;
    try {
      await api.updateSession(sessionId, { title: name });
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
          setVersions((m) => ({ ...m, [stepKey]: a.versions || [] }));
          setViewVersion((m) => ({ ...m, [stepKey]: 0 })); // 生成/修订后回到最新
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

  async function revise(stepKey) {
    const instruction = (reviseText[stepKey] || "").trim();
    if (!instruction) return;
    setStatus(stepKey, { running: true, percent: 0, message: "提交修订…", err: "" });
    try {
      await api.reviseStep(sessionId, stepKey, instruction);
    } catch (e) {
      setStatus(stepKey, { running: false, err: e.message });
      return;
    }
    setReviseText((m) => ({ ...m, [stepKey]: "" }));
    attachStream(stepKey); // 复用同一 SSE 通道
  }

  async function viewVersionFn(stepKey, name, v) {
    if (v === 0) {
      setViewVersion((m) => ({ ...m, [stepKey]: 0 }));
      return;
    }
    try {
      const a = await api.getArtifactVersion(sessionId, name, v);
      setViewContent((m) => ({ ...m, [stepKey]: a.content }));
      setViewVersion((m) => ({ ...m, [stepKey]: v }));
    } catch (e) {
      alert("读取版本失败：" + e.message);
    }
  }

  async function restoreVersionFn(stepKey, name, v) {
    if (!confirm(`恢复 v${v} 为当前版本？会生成一个新版本，历史仍保留。`)) return;
    try {
      const a = await api.restoreVersion(sessionId, name, v);
      setArtifacts((m) => ({ ...m, [stepKey]: a.content }));
      setViewVersion((m) => ({ ...m, [stepKey]: 0 }));
      const fresh = await api.getArtifact(sessionId, name);
      setVersions((m) => ({ ...m, [stepKey]: fresh.versions || [] }));
    } catch (e) {
      alert("恢复失败：" + e.message);
    }
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

  // 该步骤未被用户覆盖时的默认 provider/model：按 step.default_model 在已配置供应商里匹配
  // （全局默认 provider 优先），否则回退全局默认。与后端 engine._step_default 保持一致。
  function stepDefault(step) {
    const pref = step?.default_model || "";
    const dp = providers.find((p) => p.is_default);
    if (pref) {
      if (dp && dp.configured && (dp.models || []).includes(pref)) return { provider: dp.name, model: pref };
      const hit = providers.find((p) => p.configured && (p.models || []).includes(pref));
      if (hit) return { provider: hit.name, model: pref };
    }
    return { provider: dp?.name || "", model: dp?.default_model || "" };
  }

  if (!session) return <div className="empty">加载项目…</div>;
  const hasInputs = session.inputs?.length > 0;
  const hasArtifacts = (session.artifacts || []).length > 0;
  const activeDep = checkDeps(activeStep);
  const activeStatus = stepStatus[activeStep.key] || {};
  const activeContent = artifacts[activeStep.key];
  const activeModel = stepModels[activeStep.key] || {};
  const activeDefault = stepDefault(activeStep);
  const activeProviderName = activeModel.provider || activeDefault.provider || "";
  const activeProvider = providers.find((p) => p.name === activeProviderName);
  const activeModelName =
    activeModel.model ||
    (activeProviderName === activeDefault.provider ? activeDefault.model : "") ||
    activeProvider?.default_model ||
    "";
  // 版本：0 = 当前最新（artifacts），否则查看某历史版本（只读）
  const activeVersions = versions[activeStep.key] || [];
  const activeViewVersion = viewVersion[activeStep.key] || 0;
  const viewingHistory = activeViewVersion !== 0;
  const displayedContent = viewingHistory ? viewContent[activeStep.key] : activeContent;
  const latestVersionNo = activeVersions.length;

  function flashExportHint(text) {
    setExportHint(text);
    setTimeout(() => setExportHint(""), 1800);
  }

  async function downloadActiveOutput() {
    if (!activeContent) return;
    try {
      if (viewingHistory) {
        await api.downloadArtifactVersion(sessionId, activeStep.output_name, activeViewVersion);
        flashExportHint(`已开始下载「${activeStep.title}」v${activeViewVersion}`);
      } else {
        await api.downloadArtifact(sessionId, activeStep.output_name);
        flashExportHint(`已开始下载「${activeStep.title}」`);
      }
    } catch (e) {
      alert("导出失败：" + e.message);
    }
  }

  async function downloadBundle() {
    try {
      await api.downloadBundle(sessionId, session.title);
      flashExportHint("已开始打包下载");
    } catch (e) {
      alert("导出失败：" + e.message);
    }
  }

  return (
    <div className="workbench">
      <div className="wb-head">
        <button className="link" onClick={onBack}>← 返回</button>
        {editingTitle ? (
          <InlineEdit
            initial={session.title}
            className="title-edit"
            onCommit={commitRenameSession}
            onCancel={() => setEditingTitle(false)}
          />
        ) : (
          <h1 onDoubleClick={() => setEditingTitle(true)} title="双击可重命名">
            {session.title}
          </h1>
        )}
        {!editingTitle && (
          <button className="link small" onClick={() => setEditingTitle(true)} title="重命名项目">重命名</button>
        )}
        <span className="muted small">状态：{session.status}</span>
      </div>

      {/* ① 输入素材 */}
      <section className="card">
        <h2>① 输入素材</h2>
        <p className="muted small">
          转写文字稿（txt，主体）；课堂笔记照片（图片，辅助素材，自动识别后纳入撷要/笺注）。
        </p>
        <div className="upload-row">
          <input
            ref={fileRef}
            id="wb-file-input"
            type="file"
            multiple
            accept=".txt,.md,.pdf,.png,.jpg,.jpeg,.webp"
            onChange={onFiles}
            className="hidden-file"
          />
          <label htmlFor="wb-file-input" className="upload-btn">
            <span className="upload-ico">＋</span>
            <span>{uploading ? "上传中…" : "选择文件"}</span>
          </label>
          <span className="muted small">支持 txt / md / png / jpg / webp；可多选</span>
        </div>
        <div className="file-list">
          {hasInputs ? (
            session.inputs.map((f) => (
              <div className="file-row" key={f}>
                {editingFile === f ? (
                  <InlineEdit
                    initial={f.lastIndexOf(".") > 0 ? f.slice(0, f.lastIndexOf(".")) : f}
                    className="file-name-edit"
                    onCommit={(v) => commitRenameFile(f, v)}
                    onCancel={() => setEditingFile(null)}
                  />
                ) : (
                  <span
                    className="file-name"
                    title={f + "（双击可重命名）" + (isImageFile(f) ? " · 课堂笔记照片，将由图片识别模型转录" : "")}
                    onDoubleClick={() => setEditingFile(f)}
                  >
                    {isImageFile(f) && <span className="file-kind" title="图片素材">📷</span>}
                    {f}
                  </span>
                )}
                {editingFile !== f && (
                  <div className="file-actions">
                    <button className="file-act" onClick={() => setEditingFile(f)} title="重命名">改名</button>
                    <button className="file-act danger" onClick={() => onDeleteFile(f)} title="删除">删除</button>
                  </div>
                )}
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
        <div className="products-heading">
          <h2>③ 产出</h2>
          <div className="products-export-actions">
            {exportHint && <span className="muted small saved">{exportHint}</span>}
            <button
              className="ghost-btn"
              onClick={downloadBundle}
              disabled={!hasArtifacts}
              title={hasArtifacts ? "下载本项目全部已生成产出" : "暂无可导出的产出"}
            >
              打包全部
            </button>
          </div>
        </div>
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
                : sm?.model || stepDefault(step).model || "—";
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
                  className="ghost-btn"
                  disabled={!activeContent}
                  title={activeContent ? "下载当前面板显示的产出" : "尚未生成，无法下载"}
                  onClick={downloadActiveOutput}
                >
                  {viewingHistory ? "下载此版本" : "下载当前"}
                </button>
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

            {/* 版本切换器（≥2 版本时出现）*/}
            {activeContent && activeVersions.length > 1 && (
              <div className="version-bar">
                <span className="muted small">版本</span>
                {activeVersions.map((v) => {
                  const isLatest = v.version === latestVersionNo;
                  const isActive = activeViewVersion === v.version || (activeViewVersion === 0 && isLatest);
                  return (
                    <button
                      key={v.version}
                      className={"ver-chip" + (isActive ? " active" : "")}
                      title={v.note || ""}
                      onClick={() => viewVersionFn(activeStep.key, activeStep.output_name, isLatest ? 0 : v.version)}
                    >
                      v{v.version}{isLatest ? " · 当前" : ""}
                    </button>
                  );
                })}
                {viewingHistory && (
                  <>
                    <span style={{ flex: 1 }} />
                    <button
                      className="link small"
                      onClick={() => restoreVersionFn(activeStep.key, activeStep.output_name, activeViewVersion)}
                    >
                      恢复此版本为当前
                    </button>
                  </>
                )}
              </div>
            )}
            {viewingHistory && (
              <div className="ver-note muted small">
                📝 正在查看历史版本 v{activeViewVersion}
                {activeVersions.find((v) => v.version === activeViewVersion)?.note
                  ? `：${activeVersions.find((v) => v.version === activeViewVersion).note}`
                  : ""}
              </div>
            )}

            <div className="panel-content">
              <OutputView name={activeStep.output_name} content={displayedContent} />
            </div>

            {/* 持续迭代修订框：已有产出且在看最新版时可用 */}
            {activeContent && !viewingHistory && activeDep.ok && (
              <div className="revise-box">
                <textarea
                  className="revise-input"
                  rows={2}
                  placeholder={`对「${activeStep.title}」提修订意见，例如：术语表补充 WACC；第三节展开一些；语气更正式…（不会改动数字/人名/公司名）`}
                  value={reviseText[activeStep.key] || ""}
                  onChange={(e) => setReviseText((m) => ({ ...m, [activeStep.key]: e.target.value }))}
                  disabled={activeStatus.running}
                />
                <button
                  className="primary"
                  disabled={activeStatus.running || !(reviseText[activeStep.key] || "").trim()}
                  onClick={() => revise(activeStep.key)}
                >
                  {activeStatus.running ? "修订中…" : "提交修订"}
                </button>
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
