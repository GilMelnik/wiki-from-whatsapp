import { useSyncExternalStore } from "react";

// Every tool is a page of the site at `/<id>` (tool ids are `[a-z][a-z0-9_-]*`). Switching
// tools inside a tab rewrites the URL without reloading the page; every link is also a real
// URL, so it can be opened in a new tab (several tools side by side, one server).

const listeners = new Set();

export function toolUrl(toolId) {
  return `/${toolId}`;
}

function currentToolId() {
  return window.location.pathname.replace(/^\/+|\/+$/g, "") || null;
}

function subscribe(listener) {
  listeners.add(listener);
  window.addEventListener("popstate", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("popstate", listener);
  };
}

/** Show another tool in this tab; `replace: true` does not add a history entry. */
export function navigateToTool(toolId, { replace = false } = {}) {
  const method = replace ? "replaceState" : "pushState";
  window.history[method](null, "", toolUrl(toolId));
  for (const listener of [...listeners]) listener();
}

/** The tool id of the current page (its path), or null on `/`. */
export function useToolId() {
  return useSyncExternalStore(subscribe, currentToolId);
}

function isPlainLeftClick(event) {
  return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
}

/**
 * Link to another tool: `<ToolLink toolId="pii">Privacy</ToolLink>`. With `newTab` it
 * opens a second tab on the same server. This is the only way a tool refers to another tool.
 */
export function ToolLink({ toolId, newTab = false, className, children, ...rest }) {
  const tabProps = newTab ? { target: "_blank", rel: "noopener" } : {};
  return (
    <a
      href={toolUrl(toolId)}
      className={className}
      onClick={(event) => {
        if (newTab || !isPlainLeftClick(event)) return;
        event.preventDefault();
        navigateToTool(toolId);
      }}
      {...tabProps}
      {...rest}
    >
      {children}
    </a>
  );
}
