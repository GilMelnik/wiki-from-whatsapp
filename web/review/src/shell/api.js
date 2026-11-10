// The one way the web app talks to the server.
//
//   const api = createApi("pii");            // paths are relative to /api/pii
//   const page = await api.get("/claims", { filter: "pending", limit: 200 });
//   await api.post(`/claims/${encodeURIComponent(id)}/review`, { decision: "accept" });
//
// Every failure is an ApiError: `status` (0 when the server could not be reached),
// `message` (ready to show to the user) and `detail` (the server's raw `detail`).

export class ApiError extends Error {
  constructor(status, message, detail = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export const NETWORK_ERROR_MESSAGE = "אין חיבור לשרת";

function messageFrom(detail, fallback) {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    // FastAPI validation errors: [{loc, msg, type}, ...]
    return detail.map((item) => item?.msg ?? JSON.stringify(item)).join("; ");
  }
  return fallback;
}

function buildUrl(base, path, params) {
  const query = new URLSearchParams();
  for (const [name, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== null) query.set(name, String(value));
  }
  const suffix = query.toString();
  return suffix ? `${base}${path}?${suffix}` : `${base}${path}`;
}

async function request(base, method, path, { params, body, signal } = {}) {
  const init = { method, signal };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(buildUrl(base, path, params), init);
  } catch (cause) {
    if (cause?.name === "AbortError") throw cause;
    throw new ApiError(0, NETWORK_ERROR_MESSAGE);
  }
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!response.ok) {
    const detail = data?.detail ?? null;
    const fallback = response.statusText || `HTTP ${response.status}`;
    throw new ApiError(response.status, messageFrom(detail, fallback), detail);
  }
  return data;
}

/** API client for one tool (`/api/<tool>`); without a tool, for the common `/api` endpoints. */
export function createApi(tool) {
  const base = tool ? `/api/${tool}` : "/api";
  return {
    get: (path, params, options) => request(base, "GET", path, { ...options, params }),
    post: (path, body, options) => request(base, "POST", path, { ...options, body }),
    put: (path, body, options) => request(base, "PUT", path, { ...options, body }),
    patch: (path, body, options) => request(base, "PATCH", path, { ...options, body }),
    del: (path, options) => request(base, "DELETE", path, options),
  };
}
