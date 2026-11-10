// The key that triggers an action, shown on its button.
export function Key({ children }) {
  return (
    <kbd dir="ltr" className="ms-1 rounded border border-current px-1 font-mono text-[10px] opacity-70">
      {children}
    </kbd>
  );
}
