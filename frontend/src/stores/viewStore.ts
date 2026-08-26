import { create } from "zustand";

export type View = "chat" | "work" | "settings";

interface ViewStore {
  view: View;
  setView: (v: View) => void;
  backToChat: () => void;
}

export const useViewStore = create<ViewStore>((set) => ({
  view: "chat",
  setView: (v) => set({ view: v }),
  backToChat: () => set({ view: "chat" }),
}));
