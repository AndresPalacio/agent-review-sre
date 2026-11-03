import type { HTMLAttributes, ReactNode } from "react";

interface PanelProps extends HTMLAttributes<HTMLElement> {
  title?: string;
  children: ReactNode;
}

export function Panel({ title, children, className = "", ...props }: PanelProps) {
  return (
    <section className={`workbench-panel ${className}`} {...props}>
      {title && <div className="panel-title">{title}</div>}
      {children}
    </section>
  );
}
