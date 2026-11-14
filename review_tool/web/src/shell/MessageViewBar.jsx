import { formatCount } from "./format.js";
import { Kbd } from "./text.jsx";

// The strip above a message view: the switch between the two views, how many messages are
// selected, and "Clear" (Esc). The tool binds the keys (`v`, Escape) with useShortcuts.

export const MESSAGE_VIEWS = { conversation: "conversation", timeline: "timeline" };
const LABELS = { conversation: "By conversation", timeline: "Timeline" };

export function MessageViewBar({ view, onView, count, onClear, children }) {
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-slate-200 bg-white px-3 py-2 text-sm">
      <div role="group" aria-label="Message view" className="inline-flex overflow-hidden rounded-md border border-slate-300">
        {Object.values(MESSAGE_VIEWS).map((value) => (
          <button
            key={value}
            type="button"
            aria-pressed={view === value}
            onClick={() => onView(value)}
            className={`px-3 py-1 ${view === value ? "bg-blue-600 text-white" : "bg-white hover:bg-slate-100"}`}
          >
            {LABELS[value]}
          </button>
        ))}
      </div>
      <span className="text-xs text-slate-500">
        <Kbd>v</Kbd> switches
      </span>
      {count > 0 && (
        <span className="flex items-center gap-2">
          <span className="font-medium text-slate-800">{formatCount(count)} selected</span>
          <button type="button" className="btn btn-sm" onClick={onClear}>
            Clear <Kbd>Esc</Kbd>
          </button>
        </span>
      )}
      {children}
    </div>
  );
}
