import { VIEWS } from "./hooks.js";

const OPTIONS = [
  { value: VIEWS.thread, label: "לפי שיחה" },
  { value: VIEWS.chrono, label: "כרונולוגי" },
];

/** Two-state switch between the thread-focused and the chronological message view. */
export default function ViewToggle({ view, onChange }) {
  return (
    <div className="inline-flex rounded border overflow-hidden text-sm" role="group" aria-label="תצוגת הודעות">
      {OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={view === option.value}
          onClick={() => onChange(option.value)}
          className={`px-3 py-1 ${
            view === option.value ? "bg-blue-600 text-white" : "bg-white hover:bg-slate-100"
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
