import { useEffect, useState } from "react";
import { BannerProvider, BannerSlot, useBanner } from "./Banner.jsx";
import { APP_TITLE } from "./config.js";
import { ErrorBoundary } from "./ErrorBoundary.jsx";
import { RECONNECTING } from "./eventChannel.js";
import { useConnection } from "./events.js";
import { Header } from "./Header.jsx";
import { navigateToTool, ToolLink, useToolId } from "./navigation.jsx";
import { useArtifactStatus, useTools } from "./serverState.js";
import { ShortcutHelp } from "./ShortcutHelp.jsx";
import { ToastProvider } from "./Toast.jsx";
import { useShortcuts } from "./useShortcuts.js";

// The page: header, banner slot, and the tool named by `?tool=` filling the rest.
//
// A tool's Component receives `tool = { id, title, reads, writes, missing }`: `reads` and
// `writes` are the artifact keys of its Python ToolSpec, `missing` the ones that have no file
// yet (all three are [] until GET /api/tools answered).

const NO_KEYS = [];

function toolProps(entry, served) {
  return {
    id: entry.id,
    title: served?.title ?? entry.title,
    reads: served?.reads ?? NO_KEYS,
    writes: served?.writes ?? NO_KEYS,
    missing: served?.missing ?? NO_KEYS,
  };
}

/** Banners about the page as a whole: connection, the server not knowing the tool, missing inputs. */
function useShellBanners(tool, servedState) {
  const connection = useConnection();
  const { byKey } = useArtifactStatus();
  useBanner(
    "shell.connection",
    connection === RECONNECTING
      ? { tone: "danger", text: "אין חיבור לשרת. מנסה להתחבר מחדש; ייתכן שהתצוגה אינה עדכנית." }
      : null
  );
  const unknownToServer = tool && servedState.loaded && !servedState.served;
  useBanner(
    "shell.unknown-tool",
    unknownToServer
      ? {
          tone: "danger",
          text: `השרת שפועל כעת לא מכיר את הכלי "${tool.title}" (הוא הופעל מגרסה ישנה יותר). עצרו אותו והריצו שוב python -m review_tool.`,
        }
      : null
  );
  const missing = tool?.missing ?? NO_KEYS;
  useBanner(
    "shell.missing-inputs",
    missing.length > 0
      ? {
          tone: "warning",
          text: `חסרים קבצים שהכלי קורא: ${missing.map((key) => byKey[key]?.title ?? key).join(", ")}. הריצו קודם את השלב שמייצר אותם.`,
        }
      : null
  );
}

function UnknownTool({ toolId, tools }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-sm text-slate-600">
      <p>
        אין כלי בשם <bdi className="font-mono">{toolId}</bdi>. הכלים הקיימים:
      </p>
      <ul className="flex flex-wrap gap-2">
        {tools.map((tool) => (
          <li key={tool.id}>
            <ToolLink toolId={tool.id} className="btn btn-sm">
              {tool.title}
            </ToolLink>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Frame({ tools }) {
  const toolId = useToolId();
  const { tools: servedTools, loading, error } = useTools();
  const [helpOpen, setHelpOpen] = useState(false);

  useEffect(() => {
    if (!toolId && tools.length > 0) navigateToTool(tools[0].id, { replace: true });
  }, [toolId, tools]);

  const titled = tools.map((entry) => ({
    ...entry,
    title: servedTools.find((served) => served.name === entry.id)?.title ?? entry.title,
  }));
  const entry = tools.find((candidate) => candidate.id === toolId);
  const served = servedTools.find((candidate) => candidate.name === toolId);
  const tool = entry ? toolProps(entry, served) : null;
  useShellBanners(tool, { loaded: !loading && !error, served });
  useShortcuts([{ keys: "?", label: "קיצורי מקלדת", run: () => setHelpOpen(true) }]);

  useEffect(() => {
    document.title = tool ? `${tool.title} · ${APP_TITLE}` : APP_TITLE;
  }, [tool?.title]);

  const Component = entry?.Component;
  return (
    <div className="flex h-screen flex-col">
      <Header tools={titled} currentId={toolId} onHelp={() => setHelpOpen(true)} />
      <BannerSlot />
      <ErrorBoundary key={toolId}>
        {Component ? <Component tool={tool} /> : toolId && <UnknownTool toolId={toolId} tools={titled} />}
      </ErrorBoundary>
      <ShortcutHelp open={helpOpen} onClose={() => setHelpOpen(false)} />
    </div>
  );
}

/** The whole web app: `<App tools={TOOLS} />` (see src/tools/index.js). */
export function App({ tools }) {
  return (
    <ToastProvider>
      <BannerProvider>
        <Frame tools={tools} />
      </BannerProvider>
    </ToastProvider>
  );
}
