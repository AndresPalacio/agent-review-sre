import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type Theme = "dark" | "light";

interface LayoutState {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  leftOpen: boolean;
  rightOpen: boolean;
  bottomOpen: boolean;
  toggleLeft: () => void;
  toggleRight: () => void;
  toggleBottom: () => void;
}

const LayoutContext = createContext<LayoutState | null>(null);
const initialTheme = (): Theme =>
  localStorage.getItem("fin-graph-theme") === "light" ? "light" : "dark";

export function LayoutProvider({ children }: { children: ReactNode }) {
  const [theme, updateTheme] = useState<Theme>(initialTheme);
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);
  const [bottomOpen, setBottomOpen] = useState(true);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const setTheme = useCallback((value: Theme) => {
    updateTheme(value);
    localStorage.setItem("fin-graph-theme", value);
  }, []);

  const value = useMemo<LayoutState>(
    () => ({
      theme,
      setTheme,
      leftOpen,
      rightOpen,
      bottomOpen,
      toggleLeft: () => setLeftOpen((current) => !current),
      toggleRight: () => setRightOpen((current) => !current),
      toggleBottom: () => setBottomOpen((current) => !current),
    }),
    [theme, setTheme, leftOpen, rightOpen, bottomOpen],
  );
  return <LayoutContext.Provider value={value}>{children}</LayoutContext.Provider>;
}

export function useLayout(): LayoutState {
  const value = useContext(LayoutContext);
  if (!value) throw new Error("useLayout must be used inside LayoutProvider");
  return value;
}
