import React, { useEffect, useState } from "react";
import { api } from "../api";

// 板块首页：会话列表 + 新建会话（含任务前自定义提示词）。
export default function ModuleHome({ module, onOpenSession }) {
  const [sessions, setSessions] = useState([]);
  const [title, setTitle] = useState("");
  const [prePrompt, setPrePrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const refresh = () =>
    api.listSessions(module.key).then((d) => setSessions(d.sessions)).catch(() => {});

  useEffect(() => {
    refresh();
  }, [module.key]);

  async function create() {
    if (!title.trim()) return setErr("请填写会议/课程标题");
    setBusy(true);
    setErr("");
    try {
      const s = await api.createSession({
        module: module.key,
        title: title.trim(),
        pre_prompt: prePrompt.trim(),
      });
      setTitle("");
      setPrePrompt("");
      await refresh();
      onOpenSession(s.id);
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(id) {
    if (!confirm("确认删除该会话？数据将不可恢复。")) return;
    await api.deleteSession(id);
    refresh();
  }

  return (
    <div className="module-home">
      <h1>{module.name}</h1>
      <p className="muted">{module.description}</p>

      <section className="card">
        <h2>新建会话</h2>
        <label>
          会议 / 课程标题
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="如：光华《公司金融》第3讲 估值"
          />
        </label>
        <label>
          任务前·补充背景 &amp; 重点要求（可选，将注入全流程）
          <textarea
            rows={4}
            value={prePrompt}
            onChange={(e) => setPrePrompt(e.target.value)}
            placeholder="如：老师叫 张三；公司案例是宁德时代；重点体现 DCF 与可比公司法的对比；术语 WACC 必须保留英文。"
          />
        </label>
        {err && <div className="banner error">{err}</div>}
        <button className="primary" onClick={create} disabled={busy}>
          {busy ? "创建中…" : "创建并进入工作台"}
        </button>
      </section>

      <section className="card">
        <h2>历史会话（{sessions.length}）</h2>
        {sessions.length === 0 ? (
          <p className="muted">暂无会话。</p>
        ) : (
          <ul className="session-list">
            {sessions.map((s) => (
              <li key={s.id}>
                <button className="link" onClick={() => onOpenSession(s.id)}>
                  {s.title}
                </button>
                <span className="muted small">
                  {" "}
                  · {s.status} · {s.artifacts.length} 个产出
                </span>
                <button className="danger small" onClick={() => remove(s.id)}>
                  删除
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
