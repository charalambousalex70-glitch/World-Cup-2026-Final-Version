/* SweepStake Live — API client.
 * A single module that wraps REST calls + the live WebSocket.
 * Configure the backend URL via VITE_API_URL at build time, or it falls back
 * to same-origin /api which works behind a proxy.
 */

const API_BASE =
  (typeof import !== "undefined" && import.meta && import.meta.env && import.meta.env.VITE_API_URL) ||
  window.__API_URL__ ||
  ""; // empty = same origin

const API = `${API_BASE}/api/v1`;

let token = null;

export function setToken(t) {
  token = t;
}
export function getToken() {
  return token;
}

async function req(path, { method = "GET", body, auth = true } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth && token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${API}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = j.detail || msg;
    } catch {}
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  // auth
  register: (email, username, password) =>
    req("/auth/register", { method: "POST", auth: false, body: { email, username, password } }),
  login: (email, password) =>
    req("/auth/login", { method: "POST", auth: false, body: { email, password } }),
  me: () => req("/auth/me"),

  // sweepstakes
  listSweepstakes: () => req("/sweepstakes"),
  getSweepstake: (id) => req(`/sweepstakes/${id}`),
  createSweepstake: (data) => req("/sweepstakes", { method: "POST", body: data }),
  deleteSweepstake: (id) => req(`/sweepstakes/${id}`, { method: "DELETE" }),
  join: (invite_code) => req("/sweepstakes/join", { method: "POST", body: { invite_code } }),

  // draw
  runDraw: (id) => req(`/sweepstakes/${id}/draw`, { method: "POST" }),
  approveDraw: (id) => req(`/sweepstakes/${id}/draw/approve`, { method: "POST" }),

  // data
  leaderboard: (id) => req(`/sweepstakes/${id}/leaderboard`),
  fixtures: (id) => req(`/sweepstakes/${id}/fixtures`),
  syncNow: (id) => req(`/sweepstakes/${id}/sync`, { method: "POST" }),
  notifications: (id) => req(`/sweepstakes/${id}/notifications`),

  // payments
  setPayment: (sid, pid, has_paid) =>
    req(`/sweepstakes/${sid}/participants/${pid}/payment`, { method: "PATCH", body: { has_paid } }),
};

/* Live updates. Returns a close() function.
 * onEvent receives ({event, data}). */
export function connectLive(sweepstakeId, onEvent) {
  const wsBase = API_BASE
    ? API_BASE.replace(/^http/, "ws")
    : `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`;
  let ws;
  let alive = true;
  let retry = 0;

  function open() {
    ws = new WebSocket(`${wsBase}/ws/sweepstakes/${sweepstakeId}`);
    ws.onmessage = (e) => {
      try {
        onEvent(JSON.parse(e.data));
      } catch {}
    };
    ws.onclose = () => {
      if (!alive) return;
      retry = Math.min(retry + 1, 6);
      setTimeout(open, 1000 * retry); // backoff reconnect
    };
    // keepalive ping every 25s
    ws.onopen = () => {
      retry = 0;
      const ping = setInterval(() => {
        if (ws.readyState === 1) ws.send("ping");
        else clearInterval(ping);
      }, 25000);
    };
  }
  open();
  return () => {
    alive = false;
    if (ws) ws.close();
  };
}
