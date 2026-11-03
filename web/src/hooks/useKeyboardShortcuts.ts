import { useEffect } from "react";

export interface ShortcutActions {
  save?: () => void;
  remove?: () => void;
  undo?: () => void;
  redo?: () => void;
}

const hasInputFocus = (target: EventTarget | null) => {
  const element = target instanceof HTMLElement ? target : null;
  return !!element?.closest("input, textarea, select, [contenteditable='true'], [role='dialog']");
};

export function useKeyboardShortcuts(actions: ShortcutActions): void {
  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if (hasInputFocus(event.target)) return;
      const command = event.ctrlKey || event.metaKey;
      if (command && event.key.toLowerCase() === "s") {
        event.preventDefault();
        actions.save?.();
      } else if (command && event.key.toLowerCase() === "z" && event.shiftKey) {
        event.preventDefault();
        actions.redo?.();
      } else if (command && event.key.toLowerCase() === "z") {
        event.preventDefault();
        actions.undo?.();
      } else if (event.key === "Delete" || event.key === "Backspace") {
        actions.remove?.();
      }
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [actions]);
}

