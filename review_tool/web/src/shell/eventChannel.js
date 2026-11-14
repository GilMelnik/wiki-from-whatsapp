// One EventSource shared by the whole page, reopened whenever it drops.
//
// The server sends `hello` as the first frame of every connection (and again when this
// client was too slow and lost events). Every `hello` after the first is delivered with
// `resync: true`: events may have been missed, so whoever shows server data must refetch.

export const CONNECTING = "connecting";
export const OPEN = "open";
export const RECONNECTING = "reconnecting";

const ANY_TYPE = "*";

export class EventChannel {
  constructor({
    url,
    retryDelaysMs,
    createSource = (sourceUrl) => new EventSource(sourceUrl),
    setTimer = (callback, ms) => setTimeout(callback, ms),
  }) {
    this.url = url;
    this.retryDelaysMs = retryDelaysMs;
    this.createSource = createSource;
    this.setTimer = setTimer;
    this.subscribers = new Set();
    this.statusListeners = new Set();
    this.status = CONNECTING;
    this.source = null;
    this.started = false;
    this.failedAttempts = 0;
    this.hellos = 0;
  }

  /** Call `handler(event)` for events whose type is in `types` ("*" = all). Returns unsubscribe. */
  subscribe = (types, handler) => {
    const entry = { types: new Set([].concat(types)), handler };
    this.subscribers.add(entry);
    this.start();
    return () => this.subscribers.delete(entry);
  };

  subscribeStatus = (listener) => {
    this.statusListeners.add(listener);
    this.start();
    return () => this.statusListeners.delete(listener);
  };

  getStatus = () => this.status;

  start() {
    if (this.started) return;
    this.started = true;
    this.connect();
  }

  connect() {
    const source = this.createSource(this.url);
    this.source = source;
    source.onopen = () => {
      this.failedAttempts = 0;
      this.setStatus(OPEN);
    };
    source.onmessage = (message) => this.dispatch(message.data);
    source.onerror = () => {
      // The browser's own retry stops after some failures (for example an HTTP error from
      // a proxy while the server restarts), so the source is always replaced here.
      source.close();
      if (this.source !== source) return;
      this.source = null;
      this.setStatus(RECONNECTING);
      const last = this.retryDelaysMs.length - 1;
      const delay = this.retryDelaysMs[Math.min(this.failedAttempts, last)];
      this.failedAttempts += 1;
      this.setTimer(() => this.connect(), delay);
    };
  }

  dispatch(raw) {
    let event;
    try {
      event = JSON.parse(raw);
    } catch {
      return;
    }
    if (event.type === "hello") {
      this.hellos += 1;
      event.resync = this.hellos > 1;
    }
    for (const entry of [...this.subscribers]) {
      if (!entry.types.has(event.type) && !entry.types.has(ANY_TYPE)) continue;
      try {
        entry.handler(event);
      } catch (error) {
        console.error("event handler failed", event, error);
      }
    }
  }

  setStatus(status) {
    if (status === this.status) return;
    this.status = status;
    for (const listener of [...this.statusListeners]) listener();
  }
}
