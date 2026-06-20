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

export function fetchEntities(params = {}) {
  const q = new URLSearchParams(
    Object.fromEntries(
      Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== "")
    )
  );
  const suffix = q.toString() ? `?${q}` : "";
  return request(`/api/entities${suffix}`);
}

export function fetchEntity(entityId, params = {}) {
  const q = new URLSearchParams(
    Object.fromEntries(
      Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== "")
    )
  );
  const suffix = q.toString() ? `?${q}` : "";
  return request(`/api/entities/${encodeURIComponent(entityId)}${suffix}`);
}

export function setStatus(entityId, status) {
  return request(`/api/entities/${encodeURIComponent(entityId)}/status`, {
    method: "POST",
    body: JSON.stringify({ status }),
  });
}

export function setCanonical(entityId, name) {
  return request(`/api/entities/${encodeURIComponent(entityId)}/canonical`, {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export function moveMember(entityId, name, targetEntityId) {
  return request(`/api/entities/${encodeURIComponent(entityId)}/move-member`, {
    method: "POST",
    body: JSON.stringify({ name, target_entity_id: targetEntityId ?? null }),
  });
}

export function moveClaims(entityId, name, claimIds, targetEntityId) {
  return request(`/api/entities/${encodeURIComponent(entityId)}/move-claims`, {
    method: "POST",
    body: JSON.stringify({
      name,
      claim_ids: claimIds,
      target_entity_id: targetEntityId ?? null,
    }),
  });
}

export function mergeEntity(entityId, targetEntityId) {
  return request(`/api/entities/${encodeURIComponent(entityId)}/merge`, {
    method: "POST",
    body: JSON.stringify({ target_entity_id: targetEntityId }),
  });
}

export function renameEntity(entityId, canonicalName) {
  return request(`/api/entities/${encodeURIComponent(entityId)}/rename`, {
    method: "POST",
    body: JSON.stringify({ canonical_name: canonicalName }),
  });
}

export function setContacts(entityId, contacts) {
  return request(`/api/entities/${encodeURIComponent(entityId)}/contacts`, {
    method: "POST",
    body: JSON.stringify(contacts),
  });
}

export function deleteEntity(entityId) {
  return request(`/api/entities/${encodeURIComponent(entityId)}`, {
    method: "DELETE",
  });
}
