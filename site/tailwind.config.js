/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        navy: {
          900: "#030710",
          800: "#0a1128",
          700: "#111d3a",
        },
        accent: {
          DEFAULT: "#7FC8FF",
          dim: "rgba(127, 200, 255, 0.15)",
          glow: "rgba(127, 200, 255, 0.4)",
        },
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', "monospace"],
      },
    },
  },
  plugins: [],
};
