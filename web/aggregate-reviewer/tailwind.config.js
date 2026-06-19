/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Segoe UI", "Arial", "Helvetica", "sans-serif"],
      },
    },
  },
  plugins: [],
};
