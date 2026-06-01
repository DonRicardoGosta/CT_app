/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // A dark "trading terminal" palette.
        bg: "#0b0e14",
        panel: "#11151c",
        panel2: "#161b24",
        border: "#222a36",
        muted: "#7c8aa0",
        text: "#e6edf3",
        accent: "#3b82f6",
        up: "#16c784",
        down: "#ea3943",
        warn: "#f0b90b",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
