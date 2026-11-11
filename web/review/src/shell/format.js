import { LOCALE } from "./config.js";

// Formatting for the UI. All API timestamps are ISO-8601 strings.

const dateTime = new Intl.DateTimeFormat(LOCALE, { dateStyle: "medium", timeStyle: "short" });
const relative = new Intl.RelativeTimeFormat(LOCALE, { numeric: "auto" });
const number = new Intl.NumberFormat(LOCALE);

const RELATIVE_STEPS = [
  { unit: "second", seconds: 1, below: 60 },
  { unit: "minute", seconds: 60, below: 3600 },
  { unit: "hour", seconds: 3600, below: 86400 },
  { unit: "day", seconds: 86400, below: Infinity },
];
const BYTE_UNITS = ["B", "KB", "MB", "GB"];

export function formatDateTime(iso) {
  return iso ? dateTime.format(new Date(iso)) : "—";
}

/** "3 minutes ago" relative to `nowMs` (see useNow). A time after `nowMs` reads as "now". */
export function formatRelative(iso, nowMs) {
  if (!iso) return "—";
  const seconds = Math.min(0, Math.round((new Date(iso).getTime() - nowMs) / 1000));
  const step = RELATIVE_STEPS.find((candidate) => Math.abs(seconds) < candidate.below);
  return relative.format(Math.trunc(seconds / step.seconds), step.unit);
}

export function formatCount(value) {
  return number.format(value);
}

export function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return "—";
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit === 0 || value >= 100 ? 0 : 1;
  return `${value.toFixed(digits)} ${BYTE_UNITS[unit]}`;
}
