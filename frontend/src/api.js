// 后端 API 封装。开发态经 Vite 代理到 :8000，生产态同源。
const BASE = "/api";

async function req(path, options = {}) {
  const res = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  health: () => req("/health"),
  modules: () => req("/modules"),
  providers: () => req("/providers"),
  getProvider: (pid) => req(`/providers/${pid}`),
  addProvider: (body) =>
    req("/providers", { method: "POST", body: JSON.stringify(body) }),
  updateProvider: (pid, body) =>
    req(`/providers/${pid}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteProvider: (pid) => req(`/providers/${pid}`, { method: "DELETE" }),
  setDefaultProvider: (pid) =>
    req(`/providers/${pid}/default`, { method: "PUT" }),
  testProvider: (body) =>
    req("/providers/test", { method: "POST", body: JSON.stringify(body) }),
  listSessions: (module) =>
    req("/sessions" + (module ? `?module=${module}` : "")),
  createSession: (body) =>
    req("/sessions", { method: "POST", body: JSON.stringify(body) }),
  getSession: (id) => req(`/sessions/${id}`),
  updateSession: (id, body) =>
    req(`/sessions/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteSession: (id) => req(`/sessions/${id}`, { method: "DELETE" }),
  uploadInputs: async (id, fileList) => {
    const form = new FormData();
    for (const f of fileList) form.append("files", f);
    const res = await fetch(`/api/sessions/${id}/inputs`, {
      method: "POST",
      body: form, // 不手动设 Content-Type，浏览器自动带 boundary
    });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    return res.json();
  },
  deleteInput: (id, filename) =>
    req(`/sessions/${id}/inputs/${encodeURIComponent(filename)}`, { method: "DELETE" }),
  renameInput: (id, filename, new_name) =>
    req(`/sessions/${id}/inputs/${encodeURIComponent(filename)}`, {
      method: "PUT",
      body: JSON.stringify({ new_name }),
    }),
  getArtifact: (id, name) =>
    req(`/sessions/${id}/artifacts/${encodeURIComponent(name)}`),
  getArtifactVersion: (id, name, version) =>
    req(`/sessions/${id}/artifacts/${encodeURIComponent(name)}/versions/${version}`),
  restoreVersion: (id, name, version) =>
    req(`/sessions/${id}/artifacts/${encodeURIComponent(name)}/versions/${version}/restore`, {
      method: "POST",
    }),
  // 单步生成
  startStep: (id, step) =>
    req(`/sessions/${id}/run-step?step=${step}`, { method: "POST" }),
  // 单步修订（基于当前产出 + 修订意见 → 新版本）；进度复用 stepStream
  reviseStep: (id, step, instruction) =>
    req(`/sessions/${id}/revise-step?step=${step}`, {
      method: "POST",
      body: JSON.stringify({ instruction }),
    }),
  // 单步进度流（SSE）
  stepStream: (id, step) =>
    new EventSource(`/api/sessions/${id}/run-step-stream?step=${step}`),
  // 一次性查所有步骤的进度（用于刷新页面后恢复 UI）
  getProgress: (id) => req(`/sessions/${id}/progress`),
  // 设置某步骤的模型（provider/model 留空 = 重置为默认）
  setStepModel: (id, body) =>
    req(`/sessions/${id}/step-model`, { method: "PUT", body: JSON.stringify(body) }),
};
