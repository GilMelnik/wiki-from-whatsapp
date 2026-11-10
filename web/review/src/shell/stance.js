// The stance of a claim or claim group (C3 / C6) in Hebrew, with its badge colour.
//
//   const stance = stanceOf(group.stance);
//   <span className={`badge ${stance.badge}`}>{stance.label}</span>

export const STANCES = {
  positive: { label: "חיובית", badge: "badge-emerald" },
  negative: { label: "שלילית", badge: "badge-rose" },
  neutral: { label: "ניטרלית", badge: "badge-slate" },
  factual: { label: "עובדתית", badge: "badge-sky" },
};

export function stanceOf(stance) {
  return STANCES[stance] ?? { label: stance || "—", badge: "badge-slate" };
}
