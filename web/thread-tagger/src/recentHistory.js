const STORAGE_KEY = "thread_tagger_recent";
const MAX_ENTRIES = 40;

const KIND_LABELS = {
  split: "פוצל",
  merge: "מוזג",
  move: "הועבר",
  classification: "עודכן",
  new: "חדש",
};

export function kindLabel(kind) {
  return KIND_LABELS[kind] || kind;
}

export function loadRecent() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveRecent(list) {
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(list.slice(0, MAX_ENTRIES)));
}

export function addRecent(entry) {
  const list = loadRecent();
  const filtered = list.filter(
    (e) =>
      !(
        e.thread_id === entry.thread_id &&
        e.kind === entry.kind &&
        Date.now() - e.timestamp < 2000
      )
  );
  const next = [{ ...entry, timestamp: Date.now() }, ...filtered];
  saveRecent(next);
  return next;
}

export function addRecentBatch(entries) {
  let list = loadRecent();
  for (const entry of entries) {
    list = list.filter(
      (e) => !(e.thread_id === entry.thread_id && e.kind === entry.kind)
    );
    list.unshift({ ...entry, timestamp: Date.now() });
  }
  saveRecent(list);
  return list.slice(0, MAX_ENTRIES);
}

/** Threads from the same split as `threadId`, excluding `threadId`. */
export function splitSiblings(recent, threadId) {
  const entry = recent.find(
    (e) =>
      e.kind === "split" &&
      (e.thread_id === threadId ||
        e.source_id === threadId ||
        (e.related_ids || []).includes(threadId))
  );
  if (!entry) return [];
  const ids = new Set([
    entry.source_id,
    entry.thread_id,
    ...(entry.related_ids || []),
  ].filter(Boolean));
  ids.delete(threadId);
  return [...ids];
}

/** Most recent split group containing `threadId`. */
export function splitGroup(recent, threadId) {
  return recent.find(
    (e) =>
      e.kind === "split" &&
      (e.thread_id === threadId ||
        e.source_id === threadId ||
        (e.related_ids || []).includes(threadId))
  );
}

export function highlightMap(recent) {
  const map = new Map();
  for (const e of recent.slice(0, 15)) {
    if (!map.has(e.thread_id)) {
      map.set(e.thread_id, e.kind);
    }
    for (const id of e.related_ids || []) {
      if (!map.has(id)) map.set(id, e.kind);
    }
  }
  return map;
}
