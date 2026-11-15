import { useSyncExternalStore } from "react";

// Every tool is a page of the site at `/<id>` (tool ids are `[a-z][a-z0-9_-]*`). Switching
// tools inside a tab rewrites the URL without reloading the page; every link is also a real
// URL, so it can be opened in a new tab (several tools side by side, one server). A link may
// carry what to open there as query parameters (`/threads?thread=<id>`); the tool takes them
// once with `takeLinkParam`.

const listeners = new Set();

export function toolUrl(toolId, params) {
  const query = params ? new URLSearchParams(params).toString() : "";
  return query ? `/${toolId}?${query}` : `/${toolId}`;
}

/** A parameter the page was opened with, taken off the URL (a reload does not apply it again); null when absent. */
export function takeLinkParam(name) {
  const url = new URL(window.location.href);
  const value = url.searchParams.get(name);
  if (value !== null) {
    url.searchParams.delete(name);
    window.history.replaceState(null, "", `${url.pathname}${url.search}`);
  }
  return value;
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
export function navigateToTool(toolId, { replace = false, params } = {}) {
  const method = replace ? "replaceState" : "pushState";
  window.history[method](null, "", toolUrl(toolId, params));
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
 * Link to another tool: `<ToolLink toolId="pii">Privacy</ToolLink>`; `params` says what to open
 * there (`params={{ thread: id }}`). With `newTab` it opens a second tab on the same server.
 * This is the only way a tool refers to another tool.
 */
export function ToolLink({ toolId, params, newTab = false, className, children, ...rest }) {
  const tabProps = newTab ? { target: "_blank", rel: "noopener" } : {};
  return (
    <a
      href={toolUrl(toolId, params)}
      className={className}
      onClick={(event) => {
        if (newTab || !isPlainLeftClick(event)) return;
        event.preventDefault();
        navigateToTool(toolId, { params });
      }}
      {...tabProps}
      {...rest}
    >
      {children}
    </a>
  );
}
