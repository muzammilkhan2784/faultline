/** Colours come from CSS variables so the dark and light themes share every class. */
const token = (name) => `rgb(var(--${name}) / <alpha-value>)`;

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg: token("bg"),
        surface: token("surface"),
        raised: token("raised"),
        line: token("line"),
        ink: token("ink"),
        soft: token("soft"),
        faint: token("faint"),
        accent: token("accent"),
        ok: token("ok"),
        warn: token("warn"),
        bad: token("bad"),
        info: token("info"),
        // The shockwave: distance 1, 2, 3+ from the function you changed.
        wave1: token("wave1"),
        wave2: token("wave2"),
        wave3: token("wave3"),
      },
      fontFamily: {
        sans: ['"Inter Variable"', "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono Variable"', "ui-monospace", "SFMono-Regular", "monospace"],
      },
      fontSize: { "2xs": ["0.6875rem", { lineHeight: "1rem" }] },
      boxShadow: {
        card: "0 1px 0 rgb(var(--line) / 0.6), 0 8px 24px -12px rgb(0 0 0 / 0.5)",
        glow: "0 0 0 1px rgb(var(--accent) / 0.5), 0 0 24px -4px rgb(var(--accent) / 0.45)",
      },
      keyframes: {
        ripple: {
          "0%": { transform: "scale(0.6)", opacity: "0.7" },
          "100%": { transform: "scale(2.4)", opacity: "0" },
        },
        rise: {
          "0%": { transform: "translateY(6px)", opacity: "0" },
          "100%": { transform: "translateY(0)", opacity: "1" },
        },
      },
      animation: {
        ripple: "ripple 2.4s cubic-bezier(0.2, 0.6, 0.3, 1) infinite",
        rise: "rise 0.35s ease-out both",
      },
    },
  },
  plugins: [],
};
