import React from "react";

// 板块导航：已开发板块可点；占位板块灰显标注"即将开放"。
export default function ModuleSidebar({ modules, active, onSelect }) {
  return (
    <nav className="sidebar">
      <div className="sidebar-title">场景板块</div>
      {modules.map((m) => (
        <button
          key={m.key}
          className={
            "module-item" +
            (m.key === active ? " active" : "") +
            (m.enabled ? "" : " disabled")
          }
          onClick={() => m.enabled && onSelect(m.key)}
          disabled={!m.enabled}
          title={m.description}
        >
          <span className="module-name">{m.name}</span>
          {!m.enabled && <span className="badge">即将开放</span>}
        </button>
      ))}
    </nav>
  );
}
