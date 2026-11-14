import { useCallback, useEffect, useMemo, useState } from "react";

// Several items picked at once, for bulk actions on a list (next to the single selection of
// ItemList): a checkbox per row, "pick all visible" / "drop all visible", and the actions
// that apply to every picked item.
//
//   const picking = usePicking(`${scope}:${itemId}`);   // a new list starts with nothing picked
//   <input type="checkbox" checked={picking.has(row.id)} onChange={() => picking.toggle(row.id)} />
//   const visible = rows.map((row) => row.id);
//   picking.stateOf(visible)        // "all" | "some" | "none" (for a tri-state box)
//   picking.setMany(visible, true)  // pick / drop the visible rows (picks on other pages stay)
//   picking.picked                  // ids in the order they were picked
//
// The pure helpers below are what the hook does, for tests and for state kept elsewhere.

export function togglePicked(picked, id) {
  return picked.includes(id) ? picked.filter((other) => other !== id) : [...picked, id];
}

export function setPicked(picked, ids, on) {
  if (!on) return picked.filter((id) => !ids.includes(id));
  return [...picked, ...ids.filter((id) => !picked.includes(id))];
}

/** "all" | "some" | "none": how many of `ids` are picked (no ids → "none"). */
export function pickState(picked, ids) {
  const count = ids.filter((id) => picked.includes(id)).length;
  if (count === 0) return "none";
  return count === ids.length ? "all" : "some";
}

export function usePicking(resetKey) {
  const [picked, setState] = useState([]);
  useEffect(() => setState([]), [resetKey]);
  const toggle = useCallback((id) => setState((current) => togglePicked(current, id)), []);
  const setMany = useCallback((ids, on) => setState((current) => setPicked(current, ids, on)), []);
  const clear = useCallback(() => setState([]), []);
  return useMemo(
    () => ({
      picked,
      count: picked.length,
      has: (id) => picked.includes(id),
      stateOf: (ids) => pickState(picked, ids),
      toggle,
      setMany,
      clear,
    }),
    [picked, toggle, setMany, clear]
  );
}
