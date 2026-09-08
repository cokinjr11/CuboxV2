import { useState } from "react";
import type { PlacedPiece } from "../types";
import { dimensionsLabel, formatDimensions } from "../utils/dimensions";
import { itemTypeNoun } from "../utils/itemTypeLabels";

type OrientationChangeResult = { ok: boolean; reason: string };

interface Props {
  piece: PlacedPiece;
  onRotate: () => Promise<OrientationChangeResult>;
  onTurn: () => Promise<OrientationChangeResult>;
  onSetTilt: (angle: number) => Promise<OrientationChangeResult>;
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

// Fase 5B: `_override` en None/undefined significa que este item NO trajo un
// valor explicito para esta regla -lo que se ve (`piece.stackable`/
// `.orientation_policy`) es el efectivo, resuelto con el default del plan o
// de sistema (ver core/handling_rules.py). Un badge simple evita que el
// operador confunda "esto se resolvio asi" con "el Excel decia esto".
function ruleBadge(override: unknown): { label: string; className: string } {
  return override !== null && override !== undefined
    ? { label: "Override", className: "badge-override" }
    : { label: "Inherited", className: "badge-inherited" };
}

// Fase 5C-FINAL: Tilt es PLAN-LEVEL ONLY -no existe override de item (a
// diferencia de Stackable/Orientation), asi que el Inspector nunca debe
// mostrar un badge "Override" para Tilt (seccion 12/37 del pedido): siempre
// es "Plan Default", punto.
const TILT_PLAN_DEFAULT_LABEL = "Plan Default";

// Fase 5C-FINAL, seccion 14: Current Tilt se muestra SIGNED (+12°/-12°/0°);
// Maximum Tilt se muestra sin signo (es una magnitud).
function formatSignedTilt(angle: number): string {
  return angle > 0 ? `+${angle}°` : angle < 0 ? `${angle}°` : "0°";
}

export function PieceInspector({ piece, onRotate, onTurn, onSetTilt, onRemove, onToggleLock }: Props) {
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ ok: boolean; message: string } | null>(null);
  const orientationLocked = piece.orientation_policy === "fixed";
  const stackableRule = ruleBadge(piece.stackable_override);
  const orientationRule = ruleBadge(piece.orientation_override);
  const maxTiltAngle = piece.max_tilt_angle ?? 0;
  const currentTilt = piece.tilt_angle ?? 0;

  async function handleOrientationChange(action: () => Promise<OrientationChangeResult>) {
    setBusy(true);
    setFeedback(null);
    const result = await action();
    if (!result.ok) setFeedback({ ok: false, message: result.reason });
    setBusy(false);
  }

  // Fase 5C-FINAL, seccion 6/7/13: el rango SIGNED valido es
  // [-maxTiltAngle, +maxTiltAngle] -nunca solo [0, max].
  async function handleTiltChange(angle: number) {
    const clamped = Math.min(maxTiltAngle, Math.max(-maxTiltAngle, angle));
    setBusy(true);
    setFeedback(null);
    const result = await onSetTilt(clamped);
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
        <dt>Type</dt>
        <dd>{itemTypeNoun(piece.item_type)}</dd>
        <dt>Description</dt>
        <dd>{piece.description || "-"}</dd>
        <dt>System</dt>
        <dd>{piece.system || "-"}</dd>
        <dt>Group</dt>
        <dd>{piece.group || "-"}</dd>
        {piece.item_type === "pallet" && (
          <>
            <dt>Boxes Inside</dt>
            <dd>{piece.boxes_inside ?? "-"}</dd>
          </>
        )}
        <dt>{dimensionsLabel(piece.item_type)}</dt>
        <dd>
          {formatDimensions(piece.item_type, piece.source_dimensions, piece.source_width, piece.source_height, piece.source_thickness)} mm
        </dd>
        <dt>Orientacion</dt>
        <dd>
          {piece.orientation_label} <span className={orientationRule.className}>{orientationRule.label}</span>
        </dd>
        <dt>Peso</dt>
        <dd>{piece.weight} kg</dd>
        <dt>Stackable</dt>
        <dd>
          {piece.stackable ? "Yes" : "No"} <span className={stackableRule.className}>{stackableRule.label}</span>
        </dd>
        <dt>Priority</dt>
        <dd>{piece.priority}</dd>
        {piece.allow_tilt && (
          <>
            <dt>Tilt</dt>
            <dd>
              Allowed <span className="badge-inherited">{TILT_PLAN_DEFAULT_LABEL}</span>
            </dd>
            <dt>Maximum Tilt</dt>
            <dd>
              {maxTiltAngle}° <span className="badge-inherited">{TILT_PLAN_DEFAULT_LABEL}</span>
            </dd>
            <dt>Current Tilt</dt>
            <dd>{formatSignedTilt(currentTilt)}</dd>
          </>
        )}
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
          {piece.allow_tilt && (
            <div className="undo-redo-row">
              <button className="btn" onClick={() => handleTiltChange(currentTilt - 1)} disabled={busy || currentTilt <= -maxTiltAngle}>
                − Tilt
              </button>
              <span>{formatSignedTilt(currentTilt)} (max ±{maxTiltAngle}°)</span>
              <button className="btn" onClick={() => handleTiltChange(currentTilt + 1)} disabled={busy || currentTilt >= maxTiltAngle}>
                + Tilt
              </button>
            </div>
          )}
          <button className="btn" onClick={handleRemove} disabled={busy}>
            Quitar del Load Space
          </button>
        </>
      )}

      {feedback && !feedback.ok && <p className="error">{feedback.message}</p>}
    </div>
  );
}
