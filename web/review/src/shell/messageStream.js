// The shared message views' pure helpers (no React): rows, gaps, edges and selection.
//
// A message view shows *blocks* (one per conversation, as the threads tool's /view answers):
//   {thread_id, role, size, partial, messages: [{id, datetime, sender, content, quote?,
//    reactions?, seq?, reply_to?: {id, thread_id, sender, datetime}}]}
// `seq` is a message's place in the whole chat (0, 1, 2…): a jump in `seq` between two shown
// messages means chat messages are not shown there. A row is `{key, threadId, message}`;
// the key is the WhatsApp id, which survives every split and move.

const MINUTE_MS = 60000;

function order(message) {
  return message.seq ?? message.datetime;
}

/** Rows of every distinct block, in chat order (by `seq`, else by time). */
export function streamRows(blocks) {
  const seen = new Set();
  const rows = [];
  for (const block of blocks) {
    if (!block || seen.has(block.thread_id)) continue;
    seen.add(block.thread_id);
    for (const message of block.messages || []) rows.push({ key: message.id, threadId: block.thread_id, message });
  }
  return rows.sort((a, b) => (order(a.message) < order(b.message) ? -1 : order(a.message) > order(b.message) ? 1 : 0));
}

/** Rows of one block, in its own order. */
export function blockRows(block) {
  return (block.messages || []).map((message) => ({ key: message.id, threadId: block.thread_id, message }));
}

/** Minutes from one ISO time to the next. */
export function minutesBetween(earlier, later) {
  return Math.round((new Date(later).getTime() - new Date(earlier).getTime()) / MINUTE_MS);
}

/** "12 min", "2 h 5 min", "3 days" — a pause between two messages. */
export function formatPause(minutes) {
  if (minutes < 60) return `${minutes} min`;
  if (minutes < 48 * 60) {
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return rest ? `${hours} h ${rest} min` : `${hours} h`;
  }
  return `${Math.round(minutes / 1440)} days`;
}

/** Chat messages not shown between two rows (0 without `seq`). */
export function hiddenBetween(previous, row) {
  const before = previous?.message.seq;
  const after = row.message.seq;
  return before === undefined || after === undefined ? 0 : Math.max(0, after - before - 1);
}

/**
 * Per conversation in `rows`: is it cut by an edge of what is shown? `start` when the chat
 * message right before its first shown message is not shown, `end` when the one right after
 * its last is not (the owner cannot see whether it began earlier or went on). Also the first
 * and last shown `seq`. Needs `seq`; `chatSize` = number of messages in the chat.
 */
export function threadEdges(rows, chatSize) {
  const shown = new Set(rows.map((row) => row.message.seq));
  const edges = new Map();
  for (const row of rows) {
    const seq = row.message.seq;
    if (seq === undefined) continue;
    const edge = edges.get(row.threadId) ?? { first: seq, last: seq };
    edge.first = Math.min(edge.first, seq);
    edge.last = Math.max(edge.last, seq);
    edges.set(row.threadId, edge);
  }
  for (const edge of edges.values()) {
    edge.start = edge.first > 0 && !shown.has(edge.first - 1);
    edge.end = edge.last < chatSize - 1 && !shown.has(edge.last + 1);
  }
  return edges;
}

/** The chat stretch to load next to `seq`: `step` messages before or after it, clipped. */
export function rangeNextTo(seq, side, step, chatSize, limit = side === "before" ? -1 : chatSize) {
  if (side === "before") {
    const first = Math.max(0, seq - step, limit + 1);
    return first <= seq - 1 ? [first, seq - 1] : null;
  }
  const last = Math.min(chatSize - 1, seq + step, limit - 1);
  return seq + 1 <= last ? [seq + 1, last] : null;
}

/** Where the gold window starts / ends in `rows`: `Map<rowIndex, entering>`. */
export function windowEdges(rows, inWindow) {
  const edges = new Map();
  if (!inWindow) return edges;
  for (let i = 1; i < rows.length; i++) {
    const inside = inWindow.has(rows[i].key);
    if (inside !== inWindow.has(rows[i - 1].key)) edges.set(i, inside);
  }
  return edges;
}

// ------------------------------------------------------------------ selection

/** For each row, the keys linked to it by reply chains among the shown rows (itself included). */
export function replyClosures(rows) {
  const index = new Map(rows.map((row, i) => [row.key, i]));
  const links = rows.map(() => []);
  rows.forEach((row, i) => {
    const target = index.get(row.message.reply_to?.id);
    if (target !== undefined) {
      links[i].push(target);
      links[target].push(i);
    }
  });
  return (rowIndex) => {
    const found = new Set([rowIndex]);
    const queue = [rowIndex];
    while (queue.length) {
      for (const next of links[queue.shift()]) {
        if (!found.has(next)) {
          found.add(next);
          queue.push(next);
        }
      }
    }
    return [...found].map((i) => rows[i].key);
  };
}

/** Clicking a row: deselect it when selected, else select it with its reply chain. */
export function toggleRow(selected, rows, rowIndex, closureOf) {
  const next = new Set(selected);
  const key = rows[rowIndex].key;
  if (next.has(key)) next.delete(key);
  else for (const linked of closureOf(rowIndex)) next.add(linked);
  return next;
}

/** Shift-click: everything from the anchor row to `rowIndex` (inclusive) is added. */
export function selectRange(selected, rows, anchorIndex, rowIndex) {
  const next = new Set(selected);
  for (let i = Math.min(anchorIndex, rowIndex); i <= Math.max(anchorIndex, rowIndex); i++) next.add(rows[i].key);
  return next;
}

/** The selected keys that are on screen, grouped by conversation: `Map<threadId, keys>`. */
export function selectedByThread(rows, selected) {
  const groups = new Map();
  for (const row of rows) {
    if (!selected.has(row.key)) continue;
    if (!groups.has(row.threadId)) groups.set(row.threadId, []);
    groups.get(row.threadId).push(row.key);
  }
  return groups;
}

/**
 * What can be done with a selection that may span conversations. `sizes`: conversation id →
 * its whole number of messages. `moveTo(id)` and `newThread` are null when allowed, else the
 * reason, in a sentence.
 */
export function selectionRules(groups, sizes) {
  const keys = [...groups.values()].flat();
  const threads = [...groups.keys()];
  const whole = threads.length === 1 && groups.get(threads[0]).length === sizes.get(threads[0]);
  const none = keys.length === 0 ? "Select messages first." : null;
  return {
    keys,
    count: keys.length,
    threads,
    moveTo: (threadId) =>
      none || (threads.length === 1 && threads[0] === threadId ? "The selected messages are already in this conversation." : null),
    newThread: none || (whole ? "The selection already is one whole conversation." : null),
  };
}
