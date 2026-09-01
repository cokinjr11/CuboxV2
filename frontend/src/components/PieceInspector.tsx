import { useState } from "react";
import type { PlacedPiece } from "../types";
import { dimensionsLabel, formatDimensions } from "../utils/dimensions";

type OrientationChangeResult = { ok: boolean; reason: string };

interface Props {
  piece: PlacedPiece;
  onRotate: () => Promise<OrientationChangeResult>;
  onTurn: () => Promise<OrientationChangeResult>;
  onRemove: () => Promise<void>;
  onToggleLock: () => Promise<void>;
}

// FIXED (Fase 5): el motor de orientacion hoy da EXACTAMENTE una
// orientacion valida para esta politica -ni Rotate ni Turn tienen a donde
// ir (ver backend app/core/orientation.py:_fixed_orientation/_rotate_pairs/
// _turn_pairs). No es "eje vertical fijo pero rota en el piso": es cero
// grados de libertad. Se documenta aca en vez de dejar que el usuario lo
// descubra por un 409 del backend.
function orientationHint(piece: PlacedPiece): string {
  switch (piece.orientation_policy) {
    case "fixed":
      return "Esta pieza tiene orientacion Fixed: no admite Rotate ni Turn.";
    case "upright":
      return "Esta pieza es Upright: Rotate/Turn solo la giran 90° en el piso, la dimension vertical nunca cambia.";
    case "free":
      return "Esta pieza es Free: Rotate y Turn te dan acceso a sus 6 orientaciones posibles.";
    default:
      return "Rotar y Girar combinados dan acceso a las 4 orientaciones validas; la cara Width x Height jamas queda como base.";
  }
}

export function PieceInspector({ piece, onRotate, onTurn, onRemove, onToggleLock }: Props) {
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ ok: boolean; message: string } | null>(null);
  const orientationLocked = piece.orientation_policy === "fixed";

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
        <dt>{dimensionsLabel(piece.item_type)}</dt>
        <dd>
          {formatDimensions(piece.item_type, piece.source_dimensions, piece.source_width, piece.source_height, piece.source_thickness)} mm
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

      <p className="hint">Arrastra la pieza en la vista 3D para moverla. {orientationHint(piece)}</p>

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
            <button className="btn" onClick={() => handleOrientationChange(onRotate)} disabled={busy || orientationLocked} title={orientationLocked ? "Fixed: sin cambios de orientacion" : undefined}>
              Rotar (R)
            </button>
            <button className="btn" onClick={() => handleOrientationChange(onTurn)} disabled={busy || orientationLocked} title={orientationLocked ? "Fixed: sin cambios de orientacion" : undefined}>
              Girar (T)
            </button>
          </div>
          <button className="btn" onClick={handleRemove} disabled={busy}>
            Quitar del Load Space
          </button>
        </>
      )}

      {feedback && !feedback.ok && <p className="error">{feedback.message}</p>}
    </div>
  );
}
