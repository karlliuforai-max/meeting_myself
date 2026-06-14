import React, { useEffect, useState } from "react";
import { api } from "../api";
import InlineEdit from "../components/InlineEdit.jsx";

// 板块首页：会话列表 + 极简新建（只需一个名字，默认"新项目"）。
// 补充背景/重点要求移到工作台内填写。
export default function ModuleHome({ module, onOpenSession }) {
  const [sessions, setSessions] = useState([]);
  const [title, setTitle] = useState("新项目");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [editingId, setEditingId] = useState(null); // 正在改名的项目 id（null=无）

  const refresh = () =>
    api.listSessions(module.key).then((d) => setSessions(d.sessions)).catch(() => {});

  useEffect(() => {
    refresh();
  }, [module.key]);

  async function create() {
    const name = title.trim() || "新项目";
    setBusy(true);
    setErr("");
    try {
      const s = await api.createSession({ module: module.key, title: name });
      setTitle("新项目");
      await refresh();
      onOpenSession(s.id);
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function commitRename(s, value) {
    const name = value.trim();
    setEditingId(null);
    if (!name || name === s.title) return;
    try {
      await api.updateSession(s.id, { title: name });
      refresh();
    } catch (e) {
      alert("重命名失败：" + e.message);
    }
  }

  async function remove(id) {
    if (!confirm("确认删除该项目？数据将不可恢复。")) return;
    await api.deleteSession(id);
    refresh();
  }

  return (
    <div className="module-home">
      <h1>{module.name}</h1>
      <p className="muted">{module.description}</p>

      <section className="card">
        <h2>新建项目</h2>
        <div className="create-row">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && create()}
            placeholder="新项目"
          />
          <button className="primary" onClick={create} disabled={busy}>
            {busy ? "创建中…" : "创建并进入"}
          </button>
        </div>
        {err && <div className="banner error">{err}</div>}
        <p className="muted small" style={{ marginTop: 10 }}>
          只需起个名字即可创建；素材与背景要求进入工作台后再填。
        </p>
      </section>

      <section className="card">
        <h2>历史项目（{sessions.length}）</h2>
        {sessions.length === 0 ? (
          <p className="muted">暂无项目。</p>
        ) : (
          <ul className="session-list">
            {sessions.map((s) => (
              <li key={s.id}>
                {editingId === s.id ? (
                  <InlineEdit
                    initial={s.title}
                    onCommit={(v) => commitRename(s, v)}
                    onCancel={() => setEditingId(null)}
                  />
                ) : (
                  <button
                    className="link"
                    onClick={() => onOpenSession(s.id)}
                    onDoubleClick={() => setEditingId(s.id)}
                    title="双击可重命名"
                  >
                    {s.title}
                  </button>
                )}
                {editingId !== s.id && (
                  <span className="muted small">
                    {" "}
                    · {s.status} · {s.artifacts.length} 个产出
                  </span>
                )}
                <span style={{ flex: 1 }} />
                {editingId !== s.id && (
                  <>
                    <button className="link small" onClick={() => setEditingId(s.id)}>
                      重命名
                    </button>
                    <button className="danger small" onClick={() => remove(s.id)}>
                      删除
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
