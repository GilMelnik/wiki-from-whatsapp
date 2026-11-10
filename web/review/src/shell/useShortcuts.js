import { useEffect, useRef } from "react";
import { comboFromEvent, isTypingTarget, matchBinding } from "./keys.js";

// Keyboard shortcuts.
//
//   useShortcuts([
//     { keys: "a", label: "אישור", run: accept },
//     { keys: ["j", "arrowdown"], label: "הפריט הבא", run: next },
//     { keys: "mod+s", label: "שמירה", run: save },
//   ]);
//
// Rules the shell enforces: plain keys are ignored while the user types in a field (combos
// with Ctrl/Cmd still work; `allowInInput: true` opts a binding in), everything is paused
// while a Dialog is open, and every binding with a `label` is listed in the shortcut help
// that "?" opens. See keys.js for the combo syntax.

const active = new Set();

/** Every shortcut currently bound on the page, for the help dialog. */
export function listShortcuts() {
  return [...active].flatMap((bindingsRef) => bindingsRef.current).filter((b) => b.label);
}

function dialogIsOpen() {
  return document.querySelector('[role="dialog"][aria-modal="true"]') !== null;
}

export function useShortcuts(bindings, { enabled = true } = {}) {
  const bindingsRef = useRef(bindings);
  bindingsRef.current = bindings;

  useEffect(() => {
    if (!enabled) return undefined;
    const onKeyDown = (event) => {
      if (event.defaultPrevented || event.isComposing || dialogIsOpen()) return;
      const typing = isTypingTarget(event.target);
      const binding = matchBinding(bindingsRef.current, comboFromEvent(event), typing);
      if (!binding) return;
      event.preventDefault();
      binding.run(event);
    };
    active.add(bindingsRef);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      active.delete(bindingsRef);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [enabled]);
}
