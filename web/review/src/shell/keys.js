// Keyboard combos, independent of the keyboard layout.
//
// Letters and digits are taken from the physical key (`event.code`), so "j" works while
// the Hebrew layout is active. Syntax: "j", "shift+j", "mod+s" (Ctrl or Cmd), "alt+1",
// "enter", "escape", "arrowdown", "?". Modifier order is always mod, alt, shift.

const LETTER_CODE = /^Key([A-Z])$/;
const DIGIT_CODE = /^Digit([0-9])$/;
const TYPING_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

export function comboFromEvent(event) {
  const letter = LETTER_CODE.exec(event.code ?? "");
  const digit = DIGIT_CODE.exec(event.code ?? "");
  const base = letter ? letter[1].toLowerCase() : digit ? digit[1] : (event.key ?? "").toLowerCase();
  // Shift is part of a symbol itself ("?" is Shift+/), so it only counts for other keys.
  const shiftCounts = Boolean(letter || digit) || base.length > 1;
  const parts = [];
  if (event.ctrlKey || event.metaKey) parts.push("mod");
  if (event.altKey) parts.push("alt");
  if (event.shiftKey && shiftCounts) parts.push("shift");
  parts.push(base);
  return parts.join("+");
}

/** True when the user is typing into a field, where plain keys must not trigger shortcuts. */
export function isTypingTarget(target) {
  return Boolean(target) && (target.isContentEditable === true || TYPING_TAGS.has(target.tagName));
}

/** The binding (see useShortcuts) that a combo triggers, or undefined. */
export function matchBinding(bindings, combo, typing) {
  return bindings.find((binding) => {
    if (![].concat(binding.keys).includes(combo)) return false;
    return !typing || combo.startsWith("mod+") || binding.allowInInput === true;
  });
}
