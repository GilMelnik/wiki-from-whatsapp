const BASE = "";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

export function fetchMeta() {
  return request("/api/meta");
}

export function fetchStats() {
  return request("/api/stats");
}

export function fetchClaims(params) {
  const q = new URLSearchParams(params);
  return request(`/api/claims?${q}`);
}

export function fetchClaim(id, params) {
  const q = new URLSearchParams(params);
  return request(`/api/claims/${encodeURIComponent(id)}?${q}`);
}

export function reviewClaim(id, decision) {
  return request(`/api/claims/${encodeURIComponent(id)}/review`, {
    method: "POST",
    body: JSON.stringify({ decision }),
  });
}
