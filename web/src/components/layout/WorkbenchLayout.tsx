import type { ReactNode } from "react";
import { useLayout } from "../../contexts/LayoutContext";

interface WorkbenchLayoutProps {
  top: ReactNode;
  left: ReactNode;
  canvas: ReactNode;
  right: ReactNode;
  bottom: ReactNode;
}

export function WorkbenchLayout({ top, left, canvas, right, bottom }: WorkbenchLayoutProps) {
  const { leftOpen, rightOpen, bottomOpen } = useLayout();
  return (
    <div className="workbench-shell">
      {top}
      <div className="workbench-grid" data-left={leftOpen} data-right={rightOpen} data-bottom={bottomOpen}>
        {leftOpen && <nav className="left-panel" aria-label="Flujos">{left}</nav>}
        <main className="canvas-panel">{canvas}</main>
        {rightOpen && <aside className="right-panel" aria-label="Componentes">{right}</aside>}
        {bottomOpen && <section className="bottom-panel" aria-label="Resultados">{bottom}</section>}
      </div>
    </div>
  );
}
