// Per-member colors: distinct, light backgrounds that read in RTL Hebrew.
const PALETTE = [
  { bg: "#eff6ff", border: "#bfdbfe", dot: "#3b82f6" }, // blue
  { bg: "#f0fdf4", border: "#bbf7d0", dot: "#22c55e" }, // green
  { bg: "#fef2f2", border: "#fecaca", dot: "#ef4444" }, // red
  { bg: "#fefce8", border: "#fef08a", dot: "#eab308" }, // yellow
  { bg: "#faf5ff", border: "#e9d5ff", dot: "#a855f7" }, // purple
  { bg: "#fff7ed", border: "#fed7aa", dot: "#f97316" }, // orange
  { bg: "#f0fdfa", border: "#99f6e4", dot: "#14b8a6" }, // teal
  { bg: "#fdf2f8", border: "#fbcfe8", dot: "#ec4899" }, // pink
];

export function memberColor(index) {
  return PALETTE[((index % PALETTE.length) + PALETTE.length) % PALETTE.length];
}
