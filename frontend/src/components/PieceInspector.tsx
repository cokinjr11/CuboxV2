import { useState } from "react";
import type { PlacedPiece } from "../types";

type OrientationChangeResult = { ok: boolean; reason: string };

interface Props {
  piece: PlacedPiece;
  onRotate: () => Promise<OrientationChangeResult>;
  onTurn: () => Promise<OrientationChangeResult>;
  onRemove: () => Promise<void>;
  onToggleLock: () => Promise<void>;
}

export function PieceInspector({ piece, onRotate, onTurn, onRemove, onToggleLock }: Props) {
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ ok: boolean; message: string } | null>(null);

  async function handleOrientationChange(action: () => Promise<OrientationChangeResult>) {
    setBusy(true);
    setFeedback(null);
    const result = await action();
    if (!result.ok) setFeedback({ ok: false, message: result.reason });
    setBusy(false);
  }

  async function handleRemove() {
    setBusy(true);
    await onRemove();
    setBusy(false);
  }

  async function handleToggleLock() {
    setBusy(true);
    setFeedback(null);
    await onToggleLock();
    setBusy(false);
  }

  return (
    <div className="panel">
      <h2>
        Pieza seleccionada {piece.locked && <span title="Bloqueada">🔒</span>}
      </h2>
      <dl className="details">
        <dt>Code</dt>
        <dd>{piece.code}</dd>
        <dt>Description</dt>
        <dd>{piece.description || "-"}</dd>
        <dt>System</dt>
        <dd>{piece.system || "-"}</dd>
        <dt>Group</dt>
        <dd>{piece.group || "-"}</dd>
        <dt>Dimensiones origen (W x H x T)</dt>
        <dd>
          {piece.source_width} x {piece.source_height} x {piece.source_thickness} mm
        </dd>
        <dt>Orientacion</dt>
        <dd>{piece.orientation_label}</dd>
        <dt>Peso</dt>
        <dd>{piece.weight} kg</dd>
        <dt>Stackable</dt>
        <dd>{piece.stackable ? "Yes" : "No"}</dd>
        <dt>Priority</dt>
        <dd>{piece.priority}</dd>
      </dl>

      <p className="hint">
        Arrastra la pieza en la vista 3D para moverla. Rotar y Girar combinados dan acceso a las 4 orientaciones
        validas; la cara Width x Height jamas queda como base.
      </p>

      <button className="btn" onClick={handleToggleLock} disabled={busy}>
        {piece.locked ? "🔒 Unlock" : "🔓 Lock"}
      </button>

      {piece.locked ? (
        <p className="hint">
          Esta pieza esta bloqueada (Locked): no se puede mover, rotar, girar ni quitar hasta desbloquearla.
          Optimize Remaining la respeta y no la toca.
        </p>
      ) : (
        <>
          <div className="undo-redo-row">
            <button className="btn" onClick={() => handleOrientationChange(onRotate)} disabled={busy}>
              Rotar (R)
            </button>
            <button className="btn" onClick={() => handleOrientationChange(onTurn)} disabled={busy}>
              Girar (T)
            </button>
          </div>
          <button className="btn" onClick={handleRemove} disabled={busy}>
            Quitar del contenedor
          </button>
        </>
      )}

      {feedback && !feedback.ok && <p className="error">{feedback.message}</p>}
    </div>
  );
}
