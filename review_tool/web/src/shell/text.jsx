import { plainText, textDirection } from "./direction.js";

// Direction helpers. The interface is English, left to right. Two kinds of text need care:
//
// - Content from the chat or the wiki (messages, claims, names, page titles and scopes, the
//   reviewer's reasons) is mostly Hebrew: wrap it in <Bidi>. It is set right to left with the
//   Hebrew font (lang="he") when it is Hebrew, left to right when it is Latin, and isolated
//   from the English around it, so ids, numbers and punctuation keep their place. Pass the
//   text as the children; when the children hold markup (a highlighted sentence), pass the
//   whole text as `text` so the direction can still be read from it.
//
//     <Bidi as="p" className="leading-relaxed">{claim.claim_text}</Bidi>
//     On page “<Bidi>{page.title}</Bidi>”
//
// - Identifiers, file paths, model names and numbers with units: wrap them in <Ltr>.

/** Content: direction from the text itself. `as` picks the element (default span). */
export function Bidi({ as: Element = "span", text, className, children, ...rest }) {
  const direction = textDirection(text ?? plainText(children));
  return (
    <Element dir={direction} lang={direction === "rtl" ? "he" : undefined} className={className} {...rest}>
      {children}
    </Element>
  );
}

/** An id, path or code fragment: always left to right, isolated from the text around it. */
export function Ltr({ className = "", children, ...rest }) {
  return (
    <bdi dir="ltr" className={`font-mono text-[0.9em] ${className}`} {...rest}>
      {children}
    </bdi>
  );
}

/** The key that triggers an action, shown on its button: `<Kbd>a</Kbd>`. */
export function Kbd({ children }) {
  return (
    <kbd aria-hidden="true" className="kbd">
      {children}
    </kbd>
  );
}
