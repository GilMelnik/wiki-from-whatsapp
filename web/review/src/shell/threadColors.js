// One colour and one letter per conversation, stable while the page is open (pure, no React).
//
// Every message view marks a message with its conversation: a coloured bar and a letter chip
// (A, B, C…), so colours never stand alone. The colours are theme tokens: `chip` is white
// text on a deep shade (both themes turn the scale around, so it stays readable in dark
// mode), `bar` the message's edge, `tint` a faint background. Blue, rose and amber come last:
// they also mean selection, danger and warning.

export const THREAD_COLORS = [
  { chip: "bg-teal-700 text-white", bar: "border-s-teal-600", tint: "bg-teal-50" },
  { chip: "bg-violet-700 text-white", bar: "border-s-violet-600", tint: "bg-violet-50" },
  { chip: "bg-orange-700 text-white", bar: "border-s-orange-600", tint: "bg-orange-50" },
  { chip: "bg-cyan-700 text-white", bar: "border-s-cyan-600", tint: "bg-cyan-50" },
  { chip: "bg-fuchsia-700 text-white", bar: "border-s-fuchsia-600", tint: "bg-fuchsia-50" },
  { chip: "bg-lime-700 text-white", bar: "border-s-lime-600", tint: "bg-lime-50" },
  { chip: "bg-pink-700 text-white", bar: "border-s-pink-600", tint: "bg-pink-50" },
  { chip: "bg-sky-700 text-white", bar: "border-s-sky-600", tint: "bg-sky-50" },
  { chip: "bg-yellow-700 text-white", bar: "border-s-yellow-600", tint: "bg-yellow-50" },
  { chip: "bg-emerald-700 text-white", bar: "border-s-emerald-600", tint: "bg-emerald-50" },
  { chip: "bg-blue-700 text-white", bar: "border-s-blue-600", tint: "bg-blue-50" },
  { chip: "bg-rose-700 text-white", bar: "border-s-rose-600", tint: "bg-rose-50" },
];

/** 0 → "A", 25 → "Z", 26 → "AA", … */
export function letterTag(n) {
  let tag = "";
  let rest = n;
  do {
    tag = String.fromCharCode(65 + (rest % 26)) + tag;
    rest = Math.floor(rest / 26) - 1;
  } while (rest >= 0);
  return tag;
}

/** Assigns `{chip, bar, tint, tag}` to conversation ids in the order first seen; never reassigns. */
export class ThreadColors {
  constructor(palette = THREAD_COLORS) {
    this.palette = palette;
    this.assigned = new Map();
  }

  get(threadId) {
    if (!this.assigned.has(threadId)) {
      const n = this.assigned.size;
      this.assigned.set(threadId, { ...this.palette[n % this.palette.length], tag: letterTag(n) });
    }
    return this.assigned.get(threadId);
  }

  /** The colour of a conversation that already has one, else null (assigns nothing). */
  peek(threadId) {
    return this.assigned.get(threadId) ?? null;
  }

  assignAll(threadIds) {
    for (const id of threadIds) this.get(id);
  }
}
