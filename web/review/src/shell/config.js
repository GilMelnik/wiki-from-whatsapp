// UI constants of the shell. Server-side values (ports, timers) live in config.json.

export const APP_TITLE = "Wiki review";
// English, with day-first dates ("11 Nov 2026, 15:35").
export const LOCALE = "en-GB";
export const EVENTS_URL = "/api/events";

// Waits between attempts to reopen the event stream; the last one repeats.
export const RECONNECT_DELAYS_MS = [1000, 2000, 5000];

// How long a toast stays, by tone.
export const TOAST_MS = { info: 3000, success: 3000, error: 7000 };

// Column widths in pixels. A tool may override any of these per column (see Layout).
export const PANEL_WIDTHS = {
  list: { initial: 300, min: 200, max: 560 },
  side: { initial: 360, min: 260, max: 640 },
};

// Relative times ("3 minutes ago") are refreshed this often.
export const CLOCK_TICK_MS = 30000;
