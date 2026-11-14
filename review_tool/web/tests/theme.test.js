import assert from "node:assert/strict";
import { test } from "node:test";
import { plainText, textDirection } from "../src/shell/direction.js";
import { tailwindColors, themeColors } from "../src/shell/palette.js";

// WCAG 2 contrast of the colour pairs the shell's classes use, in both themes.

function luminance([r, g, b]) {
  const linear = [r, g, b].map((value) => {
    const channel = value / 255;
    return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrast(a, b) {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (light + 0.05) / (dark + 0.05);
}

function pick(colors, name) {
  if (name === "white") return colors.white;
  const [palette, shade] = name.split("-");
  return colors[palette][Number(shade)];
}

// [foreground, background, minimum]: text 4.5, faint text, focus rings and marks 3.
const PAIRS = [
  ["slate-900", "white", 4.5], ["slate-900", "slate-50", 4.5], ["slate-700", "white", 4.5],
  ["slate-600", "white", 4.5], ["slate-500", "white", 4.5], ["slate-500", "slate-50", 4.5],
  ["slate-400", "white", 3], ["slate-900", "blue-50", 4.5], ["slate-800", "slate-100", 4.5],
  ["white", "blue-600", 4.5], ["white", "rose-600", 4.5], ["white", "emerald-700", 4.5], ["white", "slate-800", 4.5],
  ["slate-600", "slate-100", 4.5], ["sky-800", "sky-100", 4.5], ["amber-800", "amber-100", 4.5],
  ["rose-700", "rose-100", 4.5], ["emerald-700", "emerald-100", 4.5],
  ["sky-900", "sky-50", 4.5], ["amber-900", "amber-50", 4.5], ["rose-900", "rose-50", 4.5], ["emerald-900", "emerald-50", 4.5],
  ["rose-700", "white", 4.5], ["emerald-700", "white", 4.5], ["amber-800", "white", 4.5], ["sky-800", "white", 4.5],
  ["slate-900", "amber-200", 4.5], ["blue-500", "white", 3], ["blue-600", "white", 3],
];

for (const theme of ["light", "dark"]) {
  test(`text, badges, notes, buttons, marks and focus rings have enough contrast (${theme})`, () => {
    const colors = themeColors(theme);
    const failures = PAIRS.filter(([fg, bg, min]) => contrast(pick(colors, fg), pick(colors, bg)) < min).map(
      ([fg, bg, min]) => `${fg} on ${bg}: ${contrast(pick(colors, fg), pick(colors, bg)).toFixed(2)} < ${min}`
    );
    assert.deepEqual(failures, []);
  });
}

test("every palette class is a theme token", () => {
  const colors = tailwindColors();
  assert.equal(colors.white, "rgb(var(--c-white) / <alpha-value>)");
  assert.equal(colors.amber[100], "rgb(var(--c-amber-100) / <alpha-value>)");
  assert.equal(colors.red, undefined);
});

test("content direction: Hebrew is right to left, Latin left to right, the rest auto", () => {
  assert.equal(textDirection("שלום עולם"), "rtl");
  assert.equal(textDirection("IVF בארה\"ב והכנה"), "rtl");
  assert.equal(textDirection("Tammuz"), "ltr");
  assert.equal(textDirection("2024-11"), "auto");
  assert.equal(textDirection(null), "auto");
  assert.equal(plainText(["a", 1]), "a1");
  assert.equal(plainText(["a", { type: "mark" }]), null);
});
