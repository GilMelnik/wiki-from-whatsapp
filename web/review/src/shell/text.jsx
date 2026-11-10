// Direction helpers. The page is Hebrew, right to left. Two kinds of text need care:
//
// - Content from the data (messages, claims, names) can be Hebrew, English or mixed:
//   wrap it in <Bidi> so every piece takes its own direction and alignment.
// - Identifiers, file paths, model names and numbers with units are left to right even
//   inside a Hebrew sentence: wrap them in <Ltr> so punctuation does not jump around.

/** Data content: direction follows the text itself. `as` picks the element (default span). */
export function Bidi({ as: Element = "span", className, children, ...rest }) {
  return (
    <Element dir="auto" className={className} {...rest}>
      {children}
    </Element>
  );
}

/** An id, path or code fragment: always left to right, isolated from the Hebrew around it. */
export function Ltr({ className = "", children, ...rest }) {
  return (
    <bdi dir="ltr" className={`font-mono text-[0.9em] ${className}`} {...rest}>
      {children}
    </bdi>
  );
}
