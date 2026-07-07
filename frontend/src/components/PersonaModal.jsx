import React, { useEffect, useState } from "react";
import { api } from "../api";

// 全局个人画像编辑弹窗：跨项目生效；每个项目还可在工作台单独覆盖。
export default function PersonaModal({ onClose, onChanged }) {
  const [text, setText] = useState("");
  const [defaultText, setDefaultText] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [savedHint, setSavedHint] = useState("");

  useEffect(() => {
    api
      .getPersona()
      .then((d) => {
        setText(d.text || "");
        setDefaultText(d.default || "");
      })
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false));
  }, []);

  async function save() {
    setBusy(true);
    setErr("");
    try {
      const d = await api.setPersona(text);
      setText(d.text || "");
      setDefaultText(d.default || "");
      setSavedHint(text.trim() ? "已保存" : "已清空，恢复系统默认");
      setTimeout(() => setSavedHint(""), 1800);
      onChanged && onChanged();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal persona-modal" onClick={(e) => e.stopPropagation()}>
        <div className="mc-head">
          <h2>个人画像</h2>
          <button className="mc-close" onClick={onClose} title="关闭">✕</button>
        </div>
        <div className="persona-body">
          <p className="muted small">
            画像用于把纪要中「对你的启发」等内容个性化；全局画像跨项目生效，每个项目还可在工作台单独覆盖。
          </p>
          {loading ? (
            <div className="muted small">加载中…</div>
          ) : (
            <>
              <textarea
                rows={6}
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder={
                  defaultText
                    ? `未设置时按：${defaultText}`
                    : "描述你的身份、专业背景、关注点，如：商学院学生，关注估值与并购…"
                }
              />
              <p className="muted small persona-tip">
                清空文本后保存 = 恢复系统默认{defaultText ? `（${defaultText}）` : ""}。
              </p>
              {err && <div className="banner error">{err}</div>}
              <div className="row-actions">
                <button className="primary" onClick={save} disabled={busy}>
                  {busy ? "保存中…" : "保存"}
                </button>
                {savedHint && <span className="muted small saved">{savedHint}</span>}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
