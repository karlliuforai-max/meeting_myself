import React, { useEffect, useState } from "react";
import { api } from "./api";
import ModuleSidebar from "./components/ModuleSidebar.jsx";
import ModuleHome from "./pages/ModuleHome.jsx";
import Workbench from "./pages/Workbench.jsx";

export default function App() {
  const [health, setHealth] = useState(null);
  const [modules, setModules] = useState([]);
  const [activeModule, setActiveModule] = useState(null);
  const [activeSession, setActiveSession] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setError("后端未连接：" + e.message));
    api
      .modules()
      .then((d) => {
        setModules(d.modules);
        const first = d.modules.find((m) => m.enabled) || d.modules[0];
        setActiveModule(first?.key || null);
      })
      .catch((e) => setError("后端未连接：" + e.message));
  }, []);

  const current = modules.find((m) => m.key === activeModule);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">📝 会议纪要生成平台</div>
        <div className={"status " + (health ? "ok" : "down")}>
          {health ? `后端在线 · ${health.phase}` : "后端离线"}
        </div>
      </header>

      {error && <div className="banner error">{error}</div>}

      <div className="layout">
        <ModuleSidebar
          modules={modules}
          active={activeModule}
          onSelect={(k) => {
            setActiveModule(k);
            setActiveSession(null);
          }}
        />
        <main className="content">
          {!current ? (
            <div className="empty">加载中…</div>
          ) : activeSession ? (
            <Workbench
              module={current}
              sessionId={activeSession}
              onBack={() => setActiveSession(null)}
            />
          ) : (
            <ModuleHome module={current} onOpenSession={setActiveSession} />
          )}
        </main>
      </div>
    </div>
  );
}
