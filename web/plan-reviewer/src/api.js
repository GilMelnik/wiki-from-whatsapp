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

export function fetchCategories() {
  return request("/api/categories");
}

export function fetchPages() {
  return request("/api/pages");
}

export function fetchPageClaims(pageId, params = {}) {
  const q = new URLSearchParams(params);
  return request(`/api/pages/${encodeURIComponent(pageId)}/claims?${q}`);
}

export function fetchTopics() {
  return request("/api/topics");
}

export function updatePage(pageId, fields) {
  return request(`/api/pages/${encodeURIComponent(pageId)}`, {
    method: "PATCH",
    body: JSON.stringify(fields),
  });
}

export function mergePages(sourceId, targetId) {
  return request("/api/pages/merge", {
    method: "POST",
    body: JSON.stringify({ source_id: sourceId, target_id: targetId }),
  });
}

export function moveClaim({ topicId, claimKey, targetTopicId }) {
  return request("/api/claims/move", {
    method: "POST",
    body: JSON.stringify({
      topic_id: topicId,
      claim_key: claimKey,
      target_topic_id: targetTopicId,
    }),
  });
}
