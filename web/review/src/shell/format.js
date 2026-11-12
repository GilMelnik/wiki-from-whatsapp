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

// Chat times are local wall-clock times without a zone ("2024-08-15T17:05:00"): read and shown
// as they are.
const clock = new Intl.DateTimeFormat(LOCALE, { hour: "2-digit", minute: "2-digit" });
const day = new Intl.DateTimeFormat(LOCALE, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
const month = new Intl.DateTimeFormat(LOCALE, { month: "long", year: "numeric" });

/** "17:05" */
export function formatTime(iso) {
  return iso ? clock.format(new Date(iso)) : "—";
}

/** "Thu 15 Aug 2024" */
export function formatDay(iso) {
  return iso ? day.format(new Date(iso)) : "—";
}

/** "Thu, 15 Aug 2024, 17:05": the exact moment of a chat message. */
export function formatMoment(iso) {
  return iso ? `${formatDay(iso)}, ${formatTime(iso)}` : "—";
}

/** "August 2024" */
export function formatMonth(iso) {
  return iso ? month.format(new Date(iso)) : "—";
}

/** "Thu 15 Aug 2024, 17:05–19:40" (the end's day too when it differs). */
export function formatSpan(start, end) {
  const sameDay = start?.slice(0, 10) === end?.slice(0, 10);
  if (start === end) return formatMoment(start);
  return sameDay ? `${formatMoment(start)}–${formatTime(end)}` : `${formatMoment(start)} – ${formatMoment(end)}`;
}
