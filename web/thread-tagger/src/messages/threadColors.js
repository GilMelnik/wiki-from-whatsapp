/** One colour and one short letter tag per thread, stable while the page is open (no React). */

// Paul Tol's "muted" scheme (colour-blind safe) plus his "bright" blue; `ink` is the readable
// text colour on top of `color`. Every thread also gets a letter, so colours never stand alone.
export const PALETTE = [
  { color: "#332288", ink: "#ffffff" },
  { color: "#88CCEE", ink: "#0f172a" },
  { color: "#44AA99", ink: "#0f172a" },
  { color: "#117733", ink: "#ffffff" },
  { color: "#999933", ink: "#0f172a" },
  { color: "#DDCC77", ink: "#0f172a" },
  { color: "#CC6677", ink: "#0f172a" },
  { color: "#882255", ink: "#ffffff" },
  { color: "#AA4499", ink: "#ffffff" },
  { color: "#4477AA", ink: "#ffffff" },
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

/** Assigns `{color, ink, tag}` to thread ids in the order they are first seen; never reassigns. */
export class ThreadColors {
  constructor(palette = PALETTE) {
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

  /** The colour of a thread that already has one, else null (does not assign). */
  peek(threadId) {
    return this.assigned.get(threadId) || null;
  }

  assignAll(threadIds) {
    for (const id of threadIds) this.get(id);
  }
}
