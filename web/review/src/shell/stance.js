// The stance of a claim or claim group (C3 / C6), with its badge colour.
//
//   const stance = stanceOf(group.stance);
//   <span className={`badge ${stance.badge}`}>{stance.label}</span>

export const STANCES = {
  positive: { label: "Positive", badge: "badge-emerald" },
  negative: { label: "Negative", badge: "badge-rose" },
  neutral: { label: "Neutral", badge: "badge-slate" },
  factual: { label: "Factual", badge: "badge-sky" },
};

export function stanceOf(stance) {
  return STANCES[stance] ?? { label: stance || "—", badge: "badge-slate" };
}
