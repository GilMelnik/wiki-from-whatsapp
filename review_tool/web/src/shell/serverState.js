import { useMemo, useSyncExternalStore } from "react";
import { createApi } from "./api.js";
import { channel } from "./events.js";
import { createLatestGuard } from "./latest.js";

// Server state every tab shares: the tool list and the artifact status list. Each is
// fetched once for the whole page and refetched when an artifact changes or after a
// reconnect, so every component that uses the hooks below is always current.

class SharedResource {
  constructor(fetcher) {
    this.fetcher = fetcher;
    this.snapshot = { data: undefined, error: null, loading: true };
    this.listeners = new Set();
    this.guard = createLatestGuard();
    this.started = false;
  }

  subscribe = (listener) => {
    this.listeners.add(listener);
    if (!this.started) {
      this.started = true;
      this.refresh();
      channel.subscribe(["artifact_changed", "hello"], (event) => {
        if (event.type !== "hello" || event.resync) this.refresh();
      });
    }
    return () => this.listeners.delete(listener);
  };

  getSnapshot = () => this.snapshot;

  refresh = async () => {
    const isCurrent = this.guard.begin();
    let next;
    try {
      next = { data: await this.fetcher(), error: null, loading: false };
    } catch (error) {
      next = { data: this.snapshot.data, error, loading: false };
    }
    if (!isCurrent()) return;
    this.snapshot = next;
    for (const listener of [...this.listeners]) listener();
  };
}

const commonApi = createApi();
const toolsResource = new SharedResource(() => commonApi.get("/tools"));
const artifactsResource = new SharedResource(() => commonApi.get("/artifacts"));

/** `{tools: [{name, title, reads, writes, missing}], loading, error, refresh}` from GET /api/tools. */
export function useTools() {
  const { data, loading, error } = useSyncExternalStore(
    toolsResource.subscribe,
    toolsResource.getSnapshot
  );
  return useMemo(
    () => ({ tools: data ?? [], loading, error, refresh: toolsResource.refresh }),
    [data, loading, error]
  );
}

/**
 * Live artifact status from GET /api/artifacts: `{artifacts, byKey, loading, error, refresh}`.
 * Each artifact: {key, title, step, upstream, has_edited_slot, exists, mtime, edited_exists,
 * edited_mtime, stale_because}.
 */
export function useArtifactStatus() {
  const { data, loading, error } = useSyncExternalStore(
    artifactsResource.subscribe,
    artifactsResource.getSnapshot
  );
  return useMemo(() => {
    const artifacts = data ?? [];
    const byKey = Object.fromEntries(artifacts.map((artifact) => [artifact.key, artifact]));
    return { artifacts, byKey, loading, error, refresh: artifactsResource.refresh };
  }, [data, loading, error]);
}
