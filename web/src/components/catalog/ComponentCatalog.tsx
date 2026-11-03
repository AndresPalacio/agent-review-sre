import { Bot, Braces, ChevronDown, PieChart, Search, Target } from "lucide-react";
import { useMemo, useState } from "react";
import { nodeCatalog, type CatalogItem, type CatalogKind } from "../../data/node-catalog";

interface ComponentCatalogProps {
  onAdd?: (item: CatalogItem) => void;
}

const sections: Array<{ kind: CatalogKind; label: string }> = [
  { kind: "input", label: "Entradas" },
  { kind: "analyst", label: "Analistas" },
  { kind: "manager", label: "Decisiones" },
  { kind: "output", label: "Salidas" },
];

const icon = (kind: CatalogKind) => {
  if (kind === "analyst") return Bot;
  if (kind === "input") return Target;
  if (kind === "manager") return PieChart;
  return Braces;
};

export function ComponentCatalog({ onAdd }: ComponentCatalogProps) {
  const [query, setQuery] = useState("");
  const visible = useMemo(() => {
    const token = query.trim().toLocaleLowerCase();
    return token
      ? nodeCatalog.filter((item) => `${item.name} ${item.description}`.toLocaleLowerCase().includes(token))
      : nodeCatalog;
  }, [query]);
  return <>
    <div className="panel-heading"><strong>Componentes</strong></div>
    <label className="search-field"><Search size={14} /><span className="sr-only">Buscar componentes</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Buscar componentes" /></label>
    {sections.map((section) => {
      const items = visible.filter((item) => item.kind === section.kind);
      if (!items.length) return null;
      return <section className="catalog-group" key={section.kind}>
        <button type="button"><ChevronDown size={14} /> {section.label} <span>{items.length}</span></button>
        <div className="catalog-items">
          {items.map((item) => {
            const Icon = icon(item.kind);
            return <button
              type="button"
              className="catalog-item"
              key={item.key}
              draggable
              onDragStart={(event) => {
                event.dataTransfer.setData("application/fin-graph-component", item.key);
                event.dataTransfer.effectAllowed = "copy";
              }}
              onDoubleClick={() => onAdd?.(item)}
              aria-label={`Añadir ${item.name}`}
            >
              <Icon size={18} />
              <span><strong>{item.name}</strong><small>{item.description}</small></span>
            </button>;
          })}
        </div>
      </section>;
    })}
    {!visible.length && <p className="catalog-empty">No hay componentes que coincidan.</p>}
  </>;
}

