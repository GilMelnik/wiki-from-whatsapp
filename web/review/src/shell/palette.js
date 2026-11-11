import colors from "tailwindcss/colors.js";

// The colour tokens of every tool, in both themes (read by tailwind.config.js at build time,
// and by the contrast test; never imported by the app itself).
//
// Every Tailwind colour a tool may use (`bg-white`, `text-slate-600`, `border-amber-200`, the
// `badge-*` classes, ...) is a CSS variable `--c-<palette>-<shade>` holding "r g b", so the
// same class reads right in the light and the dark theme and opacity modifiers (`/40`) keep
// working. `:root` holds the light values, `:root[data-theme="dark"]` the dark ones; the
// shell sets `data-theme` (see theme.js). Palettes not listed here do not exist in the build,
// so a tool cannot pick an unthemed colour by accident; `black` stays literal (the dialog
// backdrop).
//
// Light: Tailwind's palette, except slate-400, darkened so faint text keeps 3:1 on white.
// Dark: the scale turned around (a "-700" text becomes the "-300" shade), with the lightest
// shades (50 / 100 / 200, the tinted backgrounds and borders) mixed into the dark surface
// instead, and a hand-tuned slate for the surfaces, borders and text.

export const PALETTES = [
  "slate", "blue", "sky", "amber", "rose", "emerald", "teal", "yellow", "violet", "pink", "orange", "lime", "fuchsia", "cyan",
];
export const SHADES = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950];

const LIGHT_OVERRIDES = { slate: { 400: "#7d8ba1" } };
const LIGHT_WHITE = "#ffffff";

// Dark surfaces: slate-50 is the page, white the panels, slate-100 hover and badge fills.
const DARK_WHITE = "#111a2b";
const DARK_SLATE = {
  50: "#0a101d",
  100: "#1b2537",
  200: "#28344a",
  300: "#3c4b64",
  400: "#76869e",
  500: "#94a2b6",
  600: "#aab5c6",
  700: "#c6cfdb",
  800: "#dde3eb",
  900: "#eef1f6",
  950: "#f8fafc",
};
// How much of the palette's deep shade goes into the dark surface for the tinted shades.
const DARK_TINTS = { 50: [900, 0.3], 100: [800, 0.42], 200: [700, 0.55] };

export function hexToRgb(hex) {
  const value = Number.parseInt(hex.slice(1), 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

function mix(baseHex, topHex, share) {
  const base = hexToRgb(baseHex);
  const top = hexToRgb(topHex);
  return base.map((channel, index) => Math.round(channel + (top[index] - channel) * share));
}

const MIRROR = Object.fromEntries(SHADES.map((shade, index) => [shade, SHADES[SHADES.length - 1 - index]]));

/** `{palette: {shade: [r, g, b]}, white: [r, g, b]}` of one theme ("light" | "dark"). */
export function themeColors(theme) {
  const out = { white: hexToRgb(theme === "dark" ? DARK_WHITE : LIGHT_WHITE) };
  for (const name of PALETTES) {
    const scale = colors[name];
    out[name] = Object.fromEntries(
      SHADES.map((shade) => {
        if (theme !== "dark") return [shade, hexToRgb(LIGHT_OVERRIDES[name]?.[shade] ?? scale[shade])];
        if (name === "slate") return [shade, hexToRgb(DARK_SLATE[shade])];
        const tint = DARK_TINTS[shade];
        return [shade, tint ? mix(DARK_WHITE, scale[tint[0]], tint[1]) : hexToRgb(scale[MIRROR[shade]])];
      })
    );
  }
  return out;
}

function variables(theme) {
  const values = themeColors(theme);
  const vars = { "--c-white": values.white.join(" ") };
  for (const name of PALETTES) {
    for (const shade of SHADES) vars[`--c-${name}-${shade}`] = values[name][shade].join(" ");
  }
  return vars;
}

/** The base CSS of both themes, for Tailwind's `addBase`. */
export function themeBase() {
  return {
    ":root": { ...variables("light"), colorScheme: "light" },
    ':root[data-theme="dark"]': { ...variables("dark"), colorScheme: "dark" },
  };
}

const token = (name) => `rgb(var(--c-${name}) / <alpha-value>)`;

/** Tailwind's `theme.colors`: every palette shade as its token, plus the plain keywords. */
export function tailwindColors() {
  const out = { inherit: "inherit", current: "currentColor", transparent: "transparent", black: "#000", white: token("white") };
  for (const name of PALETTES) {
    out[name] = Object.fromEntries(SHADES.map((shade) => [shade, token(`${name}-${shade}`)]));
  }
  return out;
}
