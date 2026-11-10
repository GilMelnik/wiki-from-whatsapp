import { APP_TITLE } from "./config.js";
import { OPEN, RECONNECTING } from "./eventChannel.js";
import { useConnection } from "./events.js";
import { ToolLink } from "./navigation.jsx";

const CONNECTION = {
  [OPEN]: { dot: "bg-emerald-500", label: "מחובר" },
  [RECONNECTING]: { dot: "bg-amber-500 animate-pulse", label: "מתחבר מחדש…" },
};
const CONNECTING_STATE = { dot: "bg-slate-300", label: "מתחבר…" };

function ConnectionIndicator() {
  const { dot, label } = CONNECTION[useConnection()] ?? CONNECTING_STATE;
  return (
    <span className="ms-auto flex items-center gap-1.5 text-xs text-slate-500" title="עדכונים חיים מהשרת">
      <span aria-hidden="true" className={`h-2 w-2 rounded-full ${dot}`} />
      {label}
    </span>
  );
}

/** Title, tool switcher (plain click: this tab; ↗ or Ctrl+click: a new tab), help, connection. */
export function Header({ tools, currentId, onHelp }) {
  return (
    <header className="flex shrink-0 flex-wrap items-center gap-3 border-b border-slate-200 bg-white px-4 py-2 shadow-sm">
      <h1 className="text-lg font-bold">{APP_TITLE}</h1>
      <nav aria-label="כלים" className="flex flex-wrap items-center gap-1">
        {tools.map((tool) => {
          const current = tool.id === currentId;
          return (
            <span
              key={tool.id}
              className={`flex items-center rounded text-sm ${
                current ? "bg-blue-50 text-blue-800" : "text-slate-700 hover:bg-slate-100"
              }`}
            >
              <ToolLink
                toolId={tool.id}
                aria-current={current ? "page" : undefined}
                className={`px-3 py-1 ${current ? "font-semibold" : ""}`}
              >
                {tool.title}
              </ToolLink>
              <ToolLink
                toolId={tool.id}
                newTab
                title={`פתיחת "${tool.title}" בלשונית חדשה`}
                aria-label={`פתיחת "${tool.title}" בלשונית חדשה`}
                className="pe-2 text-xs text-slate-400 hover:text-blue-700"
              >
                ↗
              </ToolLink>
            </span>
          );
        })}
      </nav>
      <ConnectionIndicator />
      <button type="button" className="btn btn-sm" title="קיצורי מקלדת (?)" onClick={onHelp}>
        ?
      </button>
    </header>
  );
}
