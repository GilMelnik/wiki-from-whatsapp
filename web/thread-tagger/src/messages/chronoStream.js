/** Merge the messages of several threads into one time-ordered stream (no React). */

/** Selection key of a message: its WhatsApp id, which survives splits and moves. */
export function messageKey(threadId, message, position) {
  return message.id ? String(message.id) : `${threadId}#${position}`;
}

/**
 * Rows `{key, threadId, position, message}` of every distinct thread, sorted by
 * `message.datetime`. Equal times keep the order of `threads`, then the order inside a thread.
 */
export function chronoStream(threads) {
  const seen = new Set();
  const rows = [];
  threads.forEach((thread, order) => {
    if (!thread || seen.has(thread.thread_id)) return;
    seen.add(thread.thread_id);
    (thread.messages || []).forEach((message, position) => {
      rows.push({
        key: messageKey(thread.thread_id, message, position),
        threadId: thread.thread_id,
        position,
        order,
        message,
      });
    });
  });
  rows.sort((a, b) => {
    const at = a.message.datetime || "";
    const bt = b.message.datetime || "";
    if (at !== bt) return at < bt ? -1 : 1;
    return a.order - b.order || a.position - b.position;
  });
  return rows.map(({ order, ...row }) => row);
}

/** The threads the thread view shows for a loaded thread: prev, the split family (or the thread), next. */
export function displayedThreads(thread, context) {
  if (!thread) return [];
  const family = context?.family || [];
  const middle = family.length > 1 ? family : [thread];
  return [context?.prev, ...middle, context?.next].filter(Boolean);
}

/**
 * Where the gold window starts / ends in `rows`: `{rowIndex: true (enters) | false (leaves)}`,
 * one entry per change of "inside the window" between consecutive rows.
 */
export function windowEdges(rows, inWindowKeys) {
  const edges = new Map();
  if (!inWindowKeys) return edges;
  for (let i = 1; i < rows.length; i++) {
    const inside = inWindowKeys.has(rows[i].key);
    if (inside !== inWindowKeys.has(rows[i - 1].key)) edges.set(i, inside);
  }
  return edges;
}
