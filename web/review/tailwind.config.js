import plugin from "tailwindcss/plugin";
import { tailwindColors, themeBase } from "./src/shell/palette.js";

// Colours are theme tokens (CSS variables, light and dark): see src/shell/palette.js.

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    colors: tailwindColors(),
    extend: {
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "Noto Sans", "Liberation Sans", "Arial", "sans-serif"],
        hebrew: ["Noto Sans Hebrew", "Segoe UI", "Arial Hebrew", "Arial", "Liberation Sans", "DejaVu Sans", "sans-serif"],
      },
    },
  },
  plugins: [plugin(({ addBase }) => addBase(themeBase()))],
};
