import { useCallback, useEffect, useSyncExternalStore } from "react";
import { readStored, storageKey, writeStored } from "./storage.js";

// Light or dark: the owner's choice ("system" by default: follow the operating system),
// remembered per browser and shared by every tab of the app. The shell writes the resolved
// theme to <html data-theme> and the colour tokens follow (palette.js). index.html applies
// the stored choice before the first paint with the same key, so a dark page never flashes.

export const THEME_CHOICES = ["system", "light", "dark"];
const KEY = storageKey("shell", "theme"); // "review.shell.theme" (also read in index.html)
const DARK_QUERY = "(prefers-color-scheme: dark)";
const listeners = new Set();
// This tab's last choice: what counts when the browser refuses storage.
let picked = "system";

function storedChoice() {
  const value = readStored(KEY, picked);
  return THEME_CHOICES.includes(value) ? value : "system";
}

function systemIsDark() {
  return window.matchMedia?.(DARK_QUERY).matches ?? false;
}

/** "light" | "dark" for a choice. */
export function resolveTheme(choice, systemDark) {
  if (choice === "light" || choice === "dark") return choice;
  return systemDark ? "dark" : "light";
}

function notify() {
  for (const listener of [...listeners]) listener();
}

function subscribe(listener) {
  listeners.add(listener);
  const media = window.matchMedia?.(DARK_QUERY);
  media?.addEventListener("change", listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    media?.removeEventListener("change", listener);
    window.removeEventListener("storage", listener);
  };
}

function snapshot() {
  return `${storedChoice()}:${resolveTheme(storedChoice(), systemIsDark())}`;
}

/** `{choice, theme, setChoice}`: what the owner picked, what is shown, and how to change it. */
export function useTheme() {
  const [choice, theme] = useSyncExternalStore(subscribe, snapshot).split(":");
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  const setChoice = useCallback((next) => {
    picked = next;
    writeStored(KEY, next);
    notify();
  }, []);
  return { choice, theme, setChoice };
}
