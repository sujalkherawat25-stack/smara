import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        display: ['"Nunito"', "system-ui", "sans-serif"],
        sans: ['"Plus Jakarta Sans"', "system-ui", "sans-serif"],
      },
      colors: {
        // Design tokens — Smara warm-midnight family
        base:     "#0b0a14",
        surface:  "#110f1c",
        elevated: "#181626",
        card:     "#141221",

        // Smara brand palette — rose gold on midnight
        smara: {
          midnight: "#1d1830",
          bronze:   "#8b6f35",
          gold:     "#C9A96E",   // primary accent
          soft:     "#d9bd87",
          cream:    "#ece4d4",
          spark:    "#6ee7b7",
          glow:     "#fbbf24",
        },

        // Entity type colors — must match TYPE_COLORS in GraphCanvas.tsx
        entity: {
          person:     "#38bdf8",  // sky-400
          location:   "#34d399",  // emerald-400
          preference: "#a78bfa",  // violet-400
          event:      "#fbbf24",  // amber-400
          concept:    "#f472b6",  // pink-400
          health:     "#f87171",  // red-400
          object:     "#94a3b8",  // slate-400
          other:      "#64748b",  // slate-500
        },
        // Operation badge colors
        operation: {
          add:    "#10b981",
          update: "#f59e0b",
          delete: "#ef4444",
          noop:   "#475569",
        },
      },
      keyframes: {
        "node-enter": {
          "0%":   { opacity: "0", transform: "scale(0.2)" },
          "70%":  { opacity: "1", transform: "scale(1.08)" },
          "100%": { transform: "scale(1)" },
        },
        "drawer-up": {
          "0%":   { transform: "translateY(100%)" },
          "100%": { transform: "translateY(0)" },
        },
        "fade-in": {
          "0%":   { opacity: "0", transform: "translateY(4px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "shimmer": {
          "0%":   { backgroundPosition: "-200% center" },
          "100%": { backgroundPosition: "200% center" },
        },
      },
      animation: {
        "node-enter": "node-enter 0.5s cubic-bezier(0.34, 1.56, 0.64, 1)",
        "drawer-up":  "drawer-up 0.28s cubic-bezier(0.32, 0.72, 0, 1)",
        "fade-in":    "fade-in 0.2s ease-out",
        "shimmer":    "shimmer 2s linear infinite",
      },
      boxShadow: {
        "gold-glow":   "0 0 20px rgba(201,169,110,0.18), 0 0 60px rgba(201,169,110,0.07)",
        "node-select": "0 0 0 2px var(--node-color,#C9A96E), 0 0 20px var(--node-glow,rgba(201,169,110,0.30))",
      },
    },
  },
  plugins: [],
} satisfies Config;
