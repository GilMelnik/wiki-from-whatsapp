// Pure helpers of the conversations tool (tested in node).
import { formatCount, formatMonth, formatSpan, streamRows, threadEdges } from "@shell";

export const TOOL_ID = "threads";
export const THREADS_KEY = "threads";
export const CLASSIFIED_KEY = "classified";
export const MODES = { all: "all", gold: "gold" };
export const SORTS = [
  { value: "time", label: "Time" },
  { value: "size", label: "Largest first" },
];
export const DEFAULT_SEARCH = { q: "", sort: "time", knowledge: "all" };
// The list filter on step 2's knowledge label (the server's `knowledge` parameter).
export const KNOWLEDGE_FILTERS = [
  { value: "all", label: "All" },
  { value: "yes", label: "Carries knowledge" },
  { value: "no", label: "No knowledge" },
  { value: "manual", label: "Set by hand" },
];
const OPENING_CHARS = 160;
const BEFORE_ROLES = ["previous", "around"];

/** The month heading of a row of the time-ordered list. */
export function monthOf(row) {
  return formatMonth(row.start_time);
}

/** "Window 3 · Thu 15 Aug 2024, 17:00–20:00 · done" */
export function windowLabel(window) {
  return `Window ${window.index + 1} · ${formatSpan(window.start, window.end)}${window.done ? " · done" : ""}`;
}

/** "3 of 8 windows done" */
export function progressText(windows) {
  return `${formatCount(windows.filter((w) => w.done).length)} of ${formatCount(windows.length)} windows done`;
}

/** Add `[first, last]` to the loaded chat stretches, merging overlaps and neighbours. */
export function addRange(ranges, range) {
  const sorted = [...ranges, range].sort((a, b) => a[0] - b[0]);
  const merged = [];
  for (const [first, last] of sorted) {
    const top = merged[merged.length - 1];
    if (top && first <= top[1] + 1) top[1] = Math.max(top[1], last);
    else merged.push([first, last]);
  }
  return merged;
}

/** The query parameters of the loaded extras: whole threads and chat stretches. */
export function extraParams(extra) {
  return { include: extra.include, context: extra.context.map(([first, last]) => `${first}-${last}`) };
}

/** The conversations of a gold window for the list: in speaking order, with what is in the
 * window and whether each may begin earlier / go on later than what is shown. */
export function windowRows(view) {
  if (!view) return [];
  const edges = threadEdges(streamRows(view.blocks), view.chat_size);
  const inside = new Set(view.in_window_ids ?? []);
  return view.blocks
    .filter((block) => block.role === "window")
    .map((block) => ({
      thread_id: block.thread_id,
      start_time: block.start_time,
      last_time: block.last_time,
      size: block.size,
      opening: (block.messages[0]?.content ?? "").replace(/\s+/g, " ").slice(0, OPENING_CHARS),
      inside: block.messages.filter((m) => inside.has(m.id)).length,
      start: edges.get(block.thread_id)?.start ?? false,
      end: edges.get(block.thread_id)?.end ?? false,
    }));
}

/** Conversations the selection can move to: every conversation in view, in view order. */
export function moveTargets(blocks, rules) {
  const seen = new Set();
  return blocks
    .filter((block) => !seen.has(block.thread_id) && seen.add(block.thread_id))
    .map((block) => ({ threadId: block.thread_id, block, reason: rules.moveTo(block.thread_id) }));
}

/**
 * The keys from one selected message to the end of its conversation (a split point), or a
 * reason why not. `blocks` are the shown ones; the message's conversation must be shown whole.
 */
export function restOfThread(blocks, key) {
  const block = blocks.find((candidate) => candidate.messages.some((m) => m.id === key));
  if (!block) return { reason: "The selected message is not shown." };
  if (block.partial) return { reason: "Only part of this conversation is shown; open it to split it." };
  const at = block.messages.findIndex((m) => m.id === key);
  if (at === 0) return { reason: "This is the first message: the whole conversation would move." };
  return { keys: block.messages.slice(at).map((m) => m.id), threadId: block.thread_id };
}

/** Merge candidates for the opened conversation, the one right before it first. */
export function mergeCandidates(blocks, currentId) {
  const others = blocks.filter((block) => block.thread_id !== currentId);
  const before = others.filter((block) => BEFORE_ROLES.includes(block.role));
  return [...before, ...others.filter((block) => !before.includes(block))];
}

/** The list request: the search as stored (an older stored search has no knowledge filter). */
export function listParams(search) {
  return { q: search.q, sort: search.sort, knowledge: search.knowledge ?? "all" };
}

/** What the list says when nothing matches. */
export function emptyListText(search) {
  const filter = KNOWLEDGE_FILTERS.find((option) => option.value === (search.knowledge ?? "all"));
  if (search.q) return `No message or sender contains “${search.q}”${filter.value === "all" ? "" : ` among “${filter.label}”`}.`;
  return filter.value === "manual" ? "No conversation has a label set by hand." : `No conversation is marked “${filter.label}”.`;
}

/** "Carries knowledge" / "No knowledge" for a label (`null` when step 2 has none). */
export function knowledgeText(label) {
  if (!label) return null;
  return label.value ? "Carries knowledge" : "No knowledge";
}

/** Step 2's own answer, quietly: its probability, or that the heuristic skipped the question. */
export function automaticText(label) {
  const answer = label.auto ? "yes" : "no";
  if (label.p_knowledge === null || label.p_knowledge === undefined) {
    return `Step 2: ${answer} (too few messages or people to ask)`;
  }
  return `Step 2: ${answer} (probability ${label.p_knowledge.toFixed(2)})`;
}
