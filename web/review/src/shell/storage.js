import { useCallback, useState } from "react";

// Per-tool state remembered in the browser (panel widths, last selection, filters).
// Keys are namespaced `review.<tool>.<name>`, so tools never collide.

export function storageKey(toolId, name) {
  return `review.${toolId}.${name}`;
}

export function readStored(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

export function writeStored(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Storage is unavailable or full: the value simply is not remembered.
  }
}

/** `useState` whose value survives reloads: `useStoredState("pii", "filter", "pending")`. */
export function useStoredState(toolId, name, initial) {
  const key = storageKey(toolId, name);
  const [value, setValue] = useState(() => readStored(key, initial));
  const update = useCallback(
    (next) => {
      setValue((previous) => {
        const resolved = typeof next === "function" ? next(previous) : next;
        writeStored(key, resolved);
        return resolved;
      });
    },
    [key]
  );
  return [value, update];
}
