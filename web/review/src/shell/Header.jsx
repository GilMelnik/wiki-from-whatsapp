import { APP_TITLE } from "./config.js";
import { OPEN, RECONNECTING } from "./eventChannel.js";
import { useConnection } from "./events.js";
import { JobIndicator } from "./JobControls.jsx";
import { ToolLink } from "./navigation.jsx";
import { Kbd } from "./text.jsx";
import { THEME_CHOICES, useTheme } from "./theme.js";

// The header: the tools (plain click: this tab; Ctrl / middle click: a new tab), the step
// that is running or ran last, the connection only when it is not live, the theme and the
// shortcuts. Nothing else: counts and states belong to the tools.

const THEME_LABELS = { system: "Auto", light: "Light", dark: "Dark" };
const THEME_TITLES = {
  system: "Follow the system's light or dark setting",
  light: "Always light",
  dark: "Always dark",
};

function ConnectionNotice() {
  const connection = useConnection();
  if (connection === OPEN) return null;
  const reconnecting = connection === RECONNECTING;
  return (
    <span className="flex items-center gap-1.5 text-xs text-slate-500" title="Live updates from the server">
      <span aria-hidden="true" className={`h-2 w-2 rounded-full ${reconnecting ? "animate-pulse bg-amber-500" : "bg-slate-300"}`} />
      {reconnecting ? "Reconnecting…" : "Connecting…"}
    </span>
  );
}

function ThemeSwitch() {
  const { choice, setChoice } = useTheme();
  return (
    <div role="radiogroup" aria-label="Theme" className="flex rounded-md border border-slate-300 p-0.5 text-xs">
      {THEME_CHOICES.map((option) => (
        <button
          key={option}
          type="button"
          role="radio"
          aria-checked={choice === option}
          title={THEME_TITLES[option]}
          className={`rounded px-2 py-0.5 ${choice === option ? "bg-slate-200 font-medium text-slate-900" : "text-slate-500 hover:text-slate-800"}`}
          onClick={() => setChoice(option)}
        >
          {THEME_LABELS[option]}
        </button>
      ))}
    </div>
  );
}

export function Header({ tools, currentId, onHelp }) {
  return (
    <header className="flex shrink-0 flex-wrap items-center gap-x-6 gap-y-2 border-b border-slate-200 bg-white px-4">
      <h1 className="py-2.5 text-sm font-semibold tracking-wide text-slate-500">{APP_TITLE}</h1>
      <nav aria-label="Tools" className="flex flex-wrap items-stretch gap-1 self-stretch">
        {tools.map((tool) => {
          const current = tool.id === currentId;
          return (
            <ToolLink
              key={tool.id}
              toolId={tool.id}
              aria-current={current ? "page" : undefined}
              title="Ctrl+click opens it in a new tab"
              className={`flex items-center border-b-2 px-3 text-sm ${
                current ? "border-blue-600 font-semibold text-slate-900" : "border-transparent text-slate-600 hover:text-slate-900"
              }`}
            >
              {tool.title}
            </ToolLink>
          );
        })}
      </nav>
      <div className="ms-auto flex items-center gap-3 py-2">
        <JobIndicator />
        <ConnectionNotice />
        <ThemeSwitch />
        <button type="button" className="btn btn-sm btn-quiet" title="Keyboard shortcuts" onClick={onHelp}>
          Shortcuts <Kbd>?</Kbd>
        </button>
      </div>
    </header>
  );
}
