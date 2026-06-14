import React, { useEffect, useRef, useState } from "react";
import { api } from "../api";

// 会话工作台。自上而下：输入素材 → 补充背景&重点要求 → 确认生成 → 输出区。
// P1 接入生成流水线，P2 接入修订窗口，P3 接入素材上传(视觉)。
export default function Workbench({ module, sessionId, onBack }) {
  const [session, setSession] = useState(null);
  const [prePrompt, setPrePrompt] = useState("");
  const [savedHint, setSavedHint] = useState("");
  const [uploading, setUploading] = useState(false);
  const [tab, setTab] = useState(module.steps[0]?.output_name);
  const fileRef = useRef(null);

  function load() {
    api.getSession(sessionId).then((s) => {
      setSession(s);
      setPrePrompt(s.pre_prompt || "");
    });
  }
  useEffect(load, [sessionId]);

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

  if (!session) return <div className="empty">加载项目…</div>;
  const hasInputs = session.inputs?.length > 0;

  return (
    <div className="workbench">
      <div className="wb-head">
        <button className="link" onClick={onBack}>
          ← 返回
        </button>
        <h1>{session.title}</h1>
        <span className="muted small">状态：{session.status}</span>
      </div>

      {/* 1. 输入素材 */}
      <section className="card">
        <h2>① 输入素材</h2>
        <p className="muted small">
          转写文字稿（txt，主体）；辅助资料（pdf）；课堂手写笔记（图片）。
        </p>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept=".txt,.md,.pdf,.png,.jpg,.jpeg"
          onChange={onFiles}
          style={{ marginTop: 6 }}
        />
        {uploading && <p className="muted small">上传中…</p>}
        <div className="file-chips">
          {hasInputs ? (
            session.inputs.map((f) => (
              <span className="chip" key={f}>
                {f}
              </span>
            ))
          ) : (
            <span className="muted small">尚未上传素材。</span>
          )}
        </div>
        <p className="muted small" style={{ marginTop: 8 }}>
          P3 将支持 PDF 辅料与手写笔记图片的理解；当前以 txt 转写稿为主。
        </p>
      </section>

      {/* 2. 补充背景 & 重点要求 */}
      <section className="card">
        <h2>② 补充背景 &amp; 重点要求</h2>
        <p className="muted small">
          可选。这里填的内容会注入纠错、章节、纪要、图谱的全流程。
        </p>
        <textarea
          rows={5}
          value={prePrompt}
          onChange={(e) => setPrePrompt(e.target.value)}
          placeholder="如：老师叫张三；公司案例是宁德时代；重点体现 DCF 与可比公司法的对比；术语 WACC 必须保留英文。"
        />
        <div className="row-actions">
          <button className="primary ghost" onClick={savePrePrompt}>
            保存背景
          </button>
          {savedHint && <span className="muted small saved">{savedHint}</span>}
        </div>
      </section>

      {/* 3. 确认生成 */}
      <section className="card generate-card">
        <div>
          <h2 style={{ marginBottom: 4 }}>③ 生成会议纪要</h2>
          <p className="muted small" style={{ margin: 0 }}>
            将依次生成：{module.steps.map((s) => s.title).join("、")}。
          </p>
        </div>
        <button
          className="primary"
          disabled={!hasInputs}
          title={hasInputs ? "" : "请先上传输入素材"}
          onClick={() => alert("生成流水线将在 P1 实现。")}
        >
          确认生成
        </button>
      </section>

      {/* 4. 输出区 */}
      <section className="card">
        <h2>④ 输出区</h2>
        <div className="tabs">
          {module.steps.map((s) => (
            <button
              key={s.output_name}
              className={"tab" + (tab === s.output_name ? " active" : "")}
              onClick={() => setTab(s.output_name)}
            >
              {s.title}
            </button>
          ))}
        </div>
        <div className="tab-body">
          <p className="muted">
            「{module.steps.find((s) => s.output_name === tab)?.title}」产出区
          </p>
          <p className="muted small">
            P1 在此渲染产出内容；P2 在此下方加入「持续修订」窗口（可反复提意见优化，保留版本历史）。
          </p>
        </div>
      </section>
    </div>
  );
}
