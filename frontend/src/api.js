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
  deleteSession: (id) => req(`/sessions/${id}`, { method: "DELETE" }),
};
