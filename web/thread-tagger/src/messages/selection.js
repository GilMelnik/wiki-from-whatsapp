/** Message selection across threads, keyed by message key (WhatsApp id); no React. */
import { buildQuoteGraph, quoteClosureForIndex } from "../quoteGraph.js";

/** For each row, the keys linked to it by quote chains (itself included). */
export function quoteClosures(rows) {
  const { quotesTarget, quotedBy } = buildQuoteGraph(rows.map((row) => row.message));
  return (rowIndex) =>
    [...quoteClosureForIndex(rowIndex, quotesTarget, quotedBy)].map((i) => rows[i].key);
}

/** Clicking a row: deselect it when selected, else select it with its quote chain. */
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
  const low = Math.min(anchorIndex, rowIndex);
  const high = Math.max(anchorIndex, rowIndex);
  for (let i = low; i <= high; i++) next.add(rows[i].key);
  return next;
}

/** Selected keys grouped by thread, in stream order: `Map<threadId, {keys, positions}>`. */
export function groupByThread(rows, selected) {
  const groups = new Map();
  for (const row of rows) {
    if (!selected.has(row.key)) continue;
    if (!groups.has(row.threadId)) groups.set(row.threadId, { keys: [], positions: [] });
    groups.get(row.threadId).keys.push(row.key);
    groups.get(row.threadId).positions.push(row.position);
  }
  return groups;
}

/** True when `positions` (sorted) has no gaps. */
export function isContiguous(positions) {
  return positions.every((p, i) => i === 0 || p === positions[i - 1] + 1);
}

/** Keys of a thread's messages at `indices` (thread view → chronological view). */
export function keysFromIndices(rows, threadId, indices) {
  return new Set(
    rows.filter((r) => r.threadId === threadId && indices.has(r.position)).map((r) => r.key)
  );
}

/** Positions inside `threadId` of the selected keys (chronological view → thread view). */
export function indicesFromKeys(rows, threadId, selected) {
  return new Set(
    rows.filter((r) => r.threadId === threadId && selected.has(r.key)).map((r) => r.position)
  );
}

/**
 * What the actions may do with a selection that can span threads.
 * `moveTo(threadId)`: null when allowed, else a reason; `newThread`, `split`, `moveDialog`: same.
 */
export function selectionRules(groups, threadSizes) {
  const count = [...groups.values()].reduce((n, g) => n + g.keys.length, 0);
  const threads = [...groups.keys()];
  const only = threads.length === 1 ? groups.get(threads[0]) : null;
  const wholeSingle = only && only.keys.length === threadSizes.get(threads[0]);
  const none = count === 0 ? "לא נבחרו הודעות" : null;
  return {
    count,
    threads,
    moveTo: (threadId) =>
      none ||
      (threads.length === 1 && threads[0] === threadId ? "כל ההודעות שנבחרו כבר בשיחה זו" : null),
    newThread: none || (wholeSingle ? "ההודעות שנבחרו הן כבר שיחה שלמה" : null),
    split:
      none ||
      (threads.length > 1
        ? `הבחירה כוללת הודעות מ-${threads.length} שיחות; פיצול פועל על שיחה אחת — השתמשו ב"שיחה חדשה" בסרגל הבחירה`
        : wholeSingle
          ? "יש להשאיר לפחות הודעה אחת בשיחה המקורית"
          : null),
    moveDialog:
      none ||
      (threads.length > 1 || !isContiguous(only.positions)
        ? "הבחירה אינה רצף בשיחה אחת — בתצוגה הכרונולוגית העבירו דרך סרגל הבחירה (לחצו על שיחת יעד)"
        : null),
  };
}
