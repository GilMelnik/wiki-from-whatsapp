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

export function fetchTopics(params = {}) {
  const q = new URLSearchParams(params);
  const suffix = q.toString() ? `?${q}` : "";
  return request(`/api/topics${suffix}`);
}

export function fetchGroups(topicId, params) {
  const q = new URLSearchParams(params);
  return request(`/api/topics/${encodeURIComponent(topicId)}/groups?${q}`);
}

export function fetchGroup(topicId, groupKey, params) {
  const q = new URLSearchParams(params);
  return request(
    `/api/topics/${encodeURIComponent(topicId)}/groups/${encodeURIComponent(groupKey)}?${q}`
  );
}

export function setRepresentative(topicId, groupKey, sourceClaimId) {
  return request(
    `/api/topics/${encodeURIComponent(topicId)}/groups/${encodeURIComponent(groupKey)}/representative`,
    {
      method: "POST",
      body: JSON.stringify({ source_claim_id: sourceClaimId }),
    }
  );
}

export function moveMember(topicId, groupKey, sourceClaimId, targetGroupKey) {
  return request(
    `/api/topics/${encodeURIComponent(topicId)}/groups/${encodeURIComponent(groupKey)}/move-member`,
    {
      method: "POST",
      body: JSON.stringify({
        source_claim_id: sourceClaimId,
        target_group_key: targetGroupKey,
      }),
    }
  );
}

export function splitCluster(topicId, groupKey, sourceClaimIds) {
  return request(
    `/api/topics/${encodeURIComponent(topicId)}/groups/${encodeURIComponent(groupKey)}/split`,
    {
      method: "POST",
      body: JSON.stringify({ source_claim_ids: sourceClaimIds }),
    }
  );
}
