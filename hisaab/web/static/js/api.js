// Tiny fetch wrapper. Every state-changing request carries X-Hisaab (the server rejects it otherwise - CSRF protection).
export class ApiError extends Error { constructor(message, status) { super(message); this.status = status; } }

async function request(method, url, body, isForm) {
  const opts = { method, headers: { "X-Hisaab": "1" }, credentials: "same-origin" };
  if (body !== undefined) {
    if (isForm) opts.body = body;
    else { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  }
  let res;
  try { res = await fetch(url, opts); } catch (e) { throw new ApiError("Cannot reach the Hisaab server. Is it still running?", 0); }
  let data = null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) data = await res.json();
  if (!res.ok) throw new ApiError((data && data.error) || `Request failed (${res.status})`, res.status);
  return data;
}

export const get = (url) => request("GET", url);
export const post = (url, body = {}) => request("POST", url, body);
export function upload(url, file, fields = {}) {
  const fd = new FormData();
  fd.append("file", file);
  for (const [k, v] of Object.entries(fields)) fd.append(k, typeof v === "string" ? v : JSON.stringify(v));
  return request("POST", url, fd, true);
}
export function uploadMany(url, files) {
  const fd = new FormData();
  for (const f of files) fd.append("file", f);
  return request("POST", url, fd, true);
}
export const qs = (obj) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(obj)) if (v !== "" && v != null && v !== false) p.set(k, v === true ? "1" : v);
  const s = p.toString();
  return s ? "?" + s : "";
};
