// The direction of a piece of content (pure, tested in node). The interface is English, left
// to right; content from the chat or the wiki is mostly Hebrew. A text is right to left when
// it has at least as many Hebrew letters as Latin ones; Latin-only text is left to right;
// text with neither (numbers, punctuation) is left to the browser ("auto").

const HEBREW = /[֐-׿יִ-ﭏ]/gu;
const LATIN = /[A-Za-zÀ-ɏ]/gu;

/** "rtl" | "ltr" | "auto" for a text. */
export function textDirection(text) {
  const value = String(text ?? "");
  const hebrew = value.match(HEBREW)?.length ?? 0;
  const latin = value.match(LATIN)?.length ?? 0;
  if (hebrew > 0 && hebrew >= latin) return "rtl";
  return latin > 0 ? "ltr" : "auto";
}

/** The plain text of React children when they are only strings and numbers, else null. */
export function plainText(children) {
  const parts = [].concat(children ?? []);
  if (!parts.every((part) => typeof part === "string" || typeof part === "number")) return null;
  return parts.join("");
}
