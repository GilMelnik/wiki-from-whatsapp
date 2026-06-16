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

export function fetchTaxonomy() {
  return request("/api/taxonomy");
}

export function fetchThreads(params) {
  const q = new URLSearchParams(params);
  return request(`/api/threads?${q}`);
}

export function fetchThread(id, params) {
  const q = new URLSearchParams(params);
  return request(`/api/threads/${encodeURIComponent(id)}?${q}`);
}

export function fetchStats(filter = "all") {
  return request(`/api/stats?filter=${filter}`);
}

export function updateClassification(id, body) {
  return request(`/api/threads/${encodeURIComponent(id)}/classification`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function mergeThreads(body) {
  return request("/api/threads/merge", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function splitThread(body) {
  return request("/api/threads/split", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function moveMessages(body) {
  return request("/api/threads/move-messages", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
