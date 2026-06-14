import React, { useEffect, useState } from "react";
import { api } from "../api";

// 会话工作台。P0：展示结构骨架（产出标签页占位）。
// P1 接入生成流水线，P2 接入修订窗口，P3 接入素材上传(视觉)。
export default function Workbench({ module, sessionId, onBack }) {
  const [session, setSession] = useState(null);
  const [tab, setTab] = useState(module.steps[0]?.output_name);

  useEffect(() => {
    api.getSession(sessionId).then(setSession).catch(() => {});
  }, [sessionId]);

  if (!session) return <div className="empty">加载会话…</div>;

  return (
    <div className="workbench">
      <div className="wb-head">
        <button className="link" onClick={onBack}>
          ← 返回
        </button>
        <h1>{session.title}</h1>
        <span className="muted small">状态：{session.status}</span>
      </div>

      {session.pre_prompt && (
        <div className="card pre-prompt">
          <strong>任务前提示：</strong>
          {session.pre_prompt}
        </div>
      )}

      <div className="card">
        <h2>输入素材</h2>
        <p className="muted small">
          已上传：{session.inputs?.length ? session.inputs.join("、") : "（无）"}
        </p>
        <p className="muted small">
          P1 将支持：上传 txt 转写稿生成四产出；P3 支持 PDF 辅料与手写笔记图片。
        </p>
      </div>

      <div className="card">
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
      </div>
    </div>
  );
}
