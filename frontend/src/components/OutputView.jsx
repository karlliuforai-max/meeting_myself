import React, { useEffect, useRef, useState } from "react";
import { marked } from "marked";
import mermaid from "mermaid";

// 全局 Mermaid 主题：Anthropic 暖米白底 + 珊瑚色点缀，节点圆角，连线柔和
// 关键：htmlLabels=false → 节点文字用原生 SVG <text> 渲染，矢量缩放永远清晰；
// 反之 true 会把文字塞进 <foreignObject> 里的 HTML，浏览器在 transform: scale 下会光栅化模糊。
mermaid.initialize({
  startOnLoad: false,
  securityLevel: "loose",
  theme: "base",
  themeVariables: {
    fontFamily:
      "ui-serif, 'Tiempos Text', Georgia, 'Songti SC', 'Noto Serif SC', serif",
    fontSize: "15px",
    background: "#FAF9F5",
    primaryColor: "#F3E3DB",
    primaryTextColor: "#1A1915",
    primaryBorderColor: "#CC785C",
    lineColor: "#B5604A",
    secondaryColor: "#F0EEE6",
    secondaryBorderColor: "#D6D0BF",
    secondaryTextColor: "#3D3A33",
    tertiaryColor: "#FFFCF5",
    tertiaryBorderColor: "#E3DED1",
    clusterBkg: "#FAF7EE",
    clusterBorder: "#D6D0BF",
    edgeLabelBackground: "#FAF9F5",
  },
  flowchart: {
    curve: "basis",
    htmlLabels: false,
    nodeSpacing: 36,
    rankSpacing: 56,
    padding: 12,
  },
});

// 渲染单个产出：.mmd → 缩放可拖拽的 Mermaid 图；其余 → Markdown。
export default function OutputView({ name, content }) {
  const isGraph = name.endsWith(".mmd");
  if (!content) {
    return <p className="muted small">尚未生成。点击右上角的「生成」按钮。</p>;
  }
  return isGraph ? <MermaidView code={content} /> : <MarkdownView md={content} />;
}

function MarkdownView({ md }) {
  const html = marked.parse(md, { breaks: true });
  return (
    <div className="markdown-body" dangerouslySetInnerHTML={{ __html: html }} />
  );
}

/* ---------- Mermaid 缩放/拖拽视图 ---------- */
function MermaidView({ code }) {
  const wrapRef = useRef(null);
  const innerRef = useRef(null);
  const [showCode, setShowCode] = useState(false);
  const [err, setErr] = useState("");
  const [zoom, setZoom] = useState(1);
  const [tx, setTx] = useState(0);
  const [ty, setTy] = useState(0);

  // 渲染 Mermaid SVG
  useEffect(() => {
    if (showCode) return;
    let alive = true;
    setErr("");
    const id = "graph-" + Math.random().toString(36).slice(2);
    mermaid
      .render(id, code)
      .then(({ svg }) => {
        if (!alive || !innerRef.current) return;
        innerRef.current.innerHTML = svg;
        const svgEl = innerRef.current.querySelector("svg");
        if (svgEl) {
          // 关键：去掉 mermaid 默认尺寸，让 SVG 用 viewBox 自适应到 stage 里；
          // 配合 preserveAspectRatio=xMidYMid meet → SVG 永远矢量缩放、不会模糊。
          svgEl.removeAttribute("width");
          svgEl.removeAttribute("height");
          svgEl.style.width = "100%";
          svgEl.style.height = "100%";
          svgEl.style.maxWidth = "none";
          svgEl.setAttribute("preserveAspectRatio", "xMidYMid meet");
        }
        // 重置视图
        setZoom(1);
        setTx(0);
        setTy(0);
      })
      .catch((e) => alive && setErr(e.message || "图谱语法有误"));
    return () => {
      alive = false;
    };
  }, [code, showCode]);

  // 滚轮缩放：以鼠标位置为锚点缩放，避免越缩越偏离视野
  // 原生 wheel 事件监听（passive:false）确保在画布上滚动时不会触发页面滚动
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || showCode) return;
    function onWheel(e) {
      e.preventDefault();
      e.stopPropagation();
      const factor = Math.exp(-e.deltaY * 0.0015);
      const rect = el.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      setZoom((z) => {
        const nz = Math.max(0.3, Math.min(6, z * factor));
        const k = nz / z;
        // 锚点：鼠标在 stage 局部坐标系下的位置 = (mx - tx) / z
        // 缩放后保持该点在屏幕上不动 → newTx = mx - k*(mx - tx)
        setTx((t) => mx - k * (mx - t));
        setTy((t) => my - k * (my - t));
        return nz;
      });
    }
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [showCode]);

  // 拖拽平移
  const drag = useRef({ active: false, sx: 0, sy: 0, ox: 0, oy: 0 });
  function onMouseDown(e) {
    if (showCode || e.button !== 0) return;
    drag.current = { active: true, sx: e.clientX, sy: e.clientY, ox: tx, oy: ty };
  }
  function onMouseMove(e) {
    if (!drag.current.active) return;
    setTx(drag.current.ox + (e.clientX - drag.current.sx));
    setTy(drag.current.oy + (e.clientY - drag.current.sy));
  }
  function endDrag() {
    drag.current.active = false;
  }

  function reset() {
    setZoom(1);
    setTx(0);
    setTy(0);
  }
  // SVG 已通过 viewBox 自适应到画布；"适配"等同重置
  const fit = reset;

  function zoomBy(factor) {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect) {
      setZoom((z) => Math.max(0.3, Math.min(6, z * factor)));
      return;
    }
    const mx = rect.width / 2;
    const my = rect.height / 2;
    setZoom((z) => {
      const nz = Math.max(0.3, Math.min(6, z * factor));
      const k = nz / z;
      setTx((t) => mx - k * (mx - t));
      setTy((t) => my - k * (my - t));
      return nz;
    });
  }

  return (
    <div>
      <div className="graph-toolbar">
        <button className="ghost-btn" onClick={() => zoomBy(1.25)}>＋</button>
        <button className="ghost-btn" onClick={() => zoomBy(1 / 1.25)}>－</button>
        <button className="ghost-btn" onClick={fit}>适配</button>
        <button className="ghost-btn" onClick={reset}>重置</button>
        <span className="muted small">{Math.round(zoom * 100)}%</span>
        <span style={{ flex: 1 }} />
        <button className="link" onClick={() => setShowCode((v) => !v)}>
          {showCode ? "查看图形" : "查看源码"}
        </button>
      </div>
      {err && <div className="banner error">图谱渲染失败：{err}</div>}
      {showCode ? (
        <pre className="code-block">{code}</pre>
      ) : (
        <div
          className="mermaid-canvas"
          ref={wrapRef}
          onMouseDown={onMouseDown}
          onMouseMove={onMouseMove}
          onMouseUp={endDrag}
          onMouseLeave={endDrag}
          title="滚轮缩放，按住左键拖拽平移"
        >
          <div
            ref={innerRef}
            className="mermaid-stage"
            style={{
              transform: `translate(${tx}px, ${ty}px) scale(${zoom})`,
              transformOrigin: "0 0",
            }}
          />
        </div>
      )}
    </div>
  );
}
