import { create } from "zustand";
import { persist } from "zustand/middleware";

interface UiState {
  sidebarCollapsed: boolean;
  mobileNavOpen: boolean;
  paletteOpen: boolean;
  contextOpen: boolean;
  activeCustomerId: string | null;
  toggleSidebar: () => void;
  setMobileNav: (open: boolean) => void;
  setPalette: (open: boolean) => void;
  setContextOpen: (open: boolean) => void;
  setActiveCustomer: (id: string | null) => void;
}

export const useUi = create<UiState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      mobileNavOpen: false,
      paletteOpen: false,
      contextOpen: false,
      activeCustomerId: null,
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setMobileNav: (mobileNavOpen) => set({ mobileNavOpen }),
      setPalette: (paletteOpen) => set({ paletteOpen }),
      setContextOpen: (contextOpen) => set({ contextOpen }),
      setActiveCustomer: (activeCustomerId) => set({ activeCustomerId }),
    }),
    { name: "bfsi-ui", partialize: (s) => ({ sidebarCollapsed: s.sidebarCollapsed }) },
  ),
);
