import { create } from "zustand";

type Theme = "dark" | "light";

interface ThemeStore {
  theme: Theme;
  toggle: () => void;
}

const saved = (localStorage.getItem("theme") as Theme) || "dark";
document.documentElement.setAttribute("data-theme", saved);

export const useThemeStore = create<ThemeStore>((set) => ({
  theme: saved,
  toggle: () =>
    set((s) => {
      const next: Theme = s.theme === "dark" ? "light" : "dark";
      localStorage.setItem("theme", next);
      document.documentElement.setAttribute("data-theme", next);
      return { theme: next };
    }),
}));
