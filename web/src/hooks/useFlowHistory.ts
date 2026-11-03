import { useCallback, useRef, useState } from "react";
import type { FlowDocument } from "../types/flow";

export function useFlowHistory(initial: FlowDocument, limit = 50) {
  const history = useRef<FlowDocument[]>([structuredClone(initial)]);
  const index = useRef(0);
  const [version, setVersion] = useState(0);

  const push = useCallback((document: FlowDocument) => {
    const value = structuredClone(document);
    if (JSON.stringify(history.current[index.current]) === JSON.stringify(value)) return;
    history.current = [...history.current.slice(0, index.current + 1), value].slice(-limit);
    index.current = history.current.length - 1;
    setVersion((current) => current + 1);
  }, [limit]);

  const undo = useCallback((): FlowDocument | null => {
    if (index.current === 0) return null;
    index.current -= 1;
    setVersion((current) => current + 1);
    return structuredClone(history.current[index.current]);
  }, []);
  const redo = useCallback((): FlowDocument | null => {
    if (index.current >= history.current.length - 1) return null;
    index.current += 1;
    setVersion((current) => current + 1);
    return structuredClone(history.current[index.current]);
  }, []);
  const reset = useCallback((document: FlowDocument) => {
    history.current = [structuredClone(document)];
    index.current = 0;
    setVersion((current) => current + 1);
  }, []);
  return {
    push, undo, redo, reset,
    canUndo: index.current > 0,
    canRedo: index.current < history.current.length - 1,
    version,
  };
}

