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
};
