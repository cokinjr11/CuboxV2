import { useEffect, useState } from "react";

// Fase 5D, seccion 28/55 del pedido: renombrar simple (campo editable
// inline) + un indicador de guardado sutil (Saving.../Saved/Save failed)
// -nunca un boton de Save manual como flujo normal, el autosave (ver
// App.tsx) es el comportamiento esperado.

interface Props {
  name: string;
  status: "idle" | "saving" | "saved" | "error";
  onRename: (name: string) => void;
}

function statusLabel(status: Props["status"]): string {
  switch (status) {
    case "saving":
      return "Saving...";
    case "saved":
      return "Saved";
    case "error":
      return "Save failed";
    default:
      return "";
  }
}

export function PlanNameBar({ name, status, onRename }: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(name);

  useEffect(() => {
    setDraft(name);
  }, [name]);

  function commit() {
    setEditing(false);
    if (draft.trim() && draft.trim() !== name) onRename(draft);
    else setDraft(name);
  }

  return (
    <div className="plan-name-bar">
      {editing ? (
        <input
          className="plan-name-input"
          value={draft}
          autoFocus
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") {
              setDraft(name);
              setEditing(false);
            }
          }}
        />
      ) : (
        <button type="button" className="plan-name-display" title="Rename plan" onClick={() => setEditing(true)}>
          {name || "Untitled Load Plan"} <span className="plan-name-edit-icon">✎</span>
        </button>
      )}
      <span className={`plan-save-status plan-save-status-${status}`}>{statusLabel(status)}</span>
    </div>
  );
}
