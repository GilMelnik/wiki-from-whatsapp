import { useEffect, useRef, useSyncExternalStore } from "react";
import { EVENTS_URL, RECONNECT_DELAYS_MS } from "./config.js";
import { EventChannel } from "./eventChannel.js";

// Server events for React components. Event shape: {type, at, key?, tool?, payload?}.
// Types: "artifact_changed" (key = artifact, payload.files = ["original" | "edited"]),
// "hello" (resync = true when events may have been missed), later "job_*".

export const channel = new EventChannel({ url: EVENTS_URL, retryDelaysMs: RECONNECT_DELAYS_MS });

/** Call `handler(event)` for every server event of the given type(s) while mounted. */
export function useEvents(types, handler) {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;
  const typeList = [].concat(types).join(" ");
  useEffect(
    () => channel.subscribe(typeList.split(" "), (event) => handlerRef.current(event)),
    [typeList]
  );
}

/**
 * Call `handler(event)` when one of the artifacts `keys` changed on disk, or when events may
 * have been missed (reconnect). This is how a tool keeps its view current: pass the keys it
 * reads and refetch in the handler. `useResource(..., { watch: keys })` does exactly that.
 */
export function useArtifactChange(keys, handler) {
  useEvents(["artifact_changed", "hello"], (event) => {
    if (event.type === "hello" ? event.resync : keys.includes(event.key)) handler(event);
  });
}

/** "connecting" | "open" | "reconnecting" */
export function useConnection() {
  return useSyncExternalStore(channel.subscribeStatus, channel.getStatus);
}
