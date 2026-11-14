import { useEffect, useRef } from "react";

// Guard against the stale-response race: the user selects A, then B; A's slow response must
// not overwrite B's panel.
//
//   const begin = useLatest();
//   async function load(id) {
//     const isCurrent = begin();              // every call makes the earlier ones stale
//     const data = await api.get(`/items/${id}`);
//     if (isCurrent()) setDetail(data);
//   }

export function createLatestGuard() {
  let current = 0;
  return {
    begin() {
      current += 1;
      const token = current;
      return () => token === current;
    },
    invalidate() {
      current += 1;
    },
  };
}

/** Returns a stable `begin()`; responses of earlier calls (and after unmount) are not current. */
export function useLatest() {
  const guardRef = useRef(null);
  if (guardRef.current === null) guardRef.current = createLatestGuard();
  const guard = guardRef.current;
  useEffect(() => guard.invalidate, [guard]);
  return guard.begin;
}
