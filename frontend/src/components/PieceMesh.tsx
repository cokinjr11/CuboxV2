import { Text } from "@react-three/drei";
import type { DragPointerEvent } from "./DragBox";
import { LoadUnitBody } from "./LoadUnitBody";
import { colorFromString } from "../utils/color";
import { pickLabelColors } from "../utils/contrast";
import { SCENE_SCALE } from "../config";
import type { DragMode } from "../hooks/useDragEngine";
import type { ColorByMode, PlacedPiece } from "../types";

const VALID_COLOR = "#3ddc84";
const INVALID_COLOR = "#ff4d4f";
const SELECTED_COLOR = "#ff6b35";
const LOCKED_EDGE_COLOR = "#e0b429";
const GRAY_PAST_COLOR = "#8a8a8a";
const CURRENT_STEP_COLOR = "#ff6b35";
const DIMMED_OPACITY = 0.15;

export type PieceVisualState = "past" | "current" | "future";

interface DragOverride {
  x: number;
  y: number;
  z: number;
  valid: boolean;
}

interface Props {
  piece: PlacedPiece;
  container: { length: number; width: number };
  selected: boolean;
  colorBy: ColorByMode;
  dragOverride: DragOverride | null;
  sequenceLabel: number | null;
  showLabels: boolean;
  /** "past"/"current"/"future" respecto de un paso de carga/descarga (el
   * slider interactivo de secuencia, o -mas adelante- una captura para las
   * guias PDF). Default "past" (aparece normal) cuando no se esta mostrando
   * ninguna secuencia. */
  visualState: PieceVisualState;
  /** Modo captura para las guias PDF (Loading/Unloading Guide): "past" pasa a
   * gris/transparente y "future" se oculta por completo, en vez del
   * tratamiento mas suave que usa el slider interactivo en vivo (donde "past"
   * se ve normal -ya esta colocada, no hace falta apagarle el color- y solo
   * "future" se atenua). Default false = comportamiento de la vista
   * interactiva de siempre. */
  guideMode?: boolean;
  onSelect: (id: string) => void;
  onBeginDrag: (
    mode: DragMode,
    pieceId: string,
    x: number,
    y: number,
    z: number,
    dx: number,
    dy: number,
    dz: number,
    stackable: boolean,
    clientX: number,
    clientY: number
  ) => void;
}

export function PieceMesh({
  piece,
  container,
  selected,
  colorBy,
  dragOverride,
  sequenceLabel,
  showLabels,
  visualState,
  guideMode = false,
  onSelect,
  onBeginDrag,
}: Props) {
  if (visualState === "future" && guideMode && !dragOverride) return null;

  const colorKey =
    colorBy === "system"
      ? piece.system
      : colorBy === "group"
        ? piece.group
        : colorBy === "priority"
          ? String(piece.priority)
          : piece.code;
  const baseColor = colorFromString(colorKey);

  const pos = dragOverride ?? { x: piece.x, y: piece.y, z: piece.z };

  // Fase 5C-FINAL, seccion 32-34 del pedido: el label debe quedar "impreso"
  // sobre la cara fisica de la pieza y rotar CON ella (ni Billboard ni
  // camara-facing) -antes usaba coordenadas de MUNDO fijas, calculadas a
  // partir de la envolvente (piece.dx/dy), asi que a cualquier angulo de
  // Tilt distinto de 0 el texto quedaba en un lugar completamente distinto
  // de donde en realidad esta la cara fisica de la pieza (y, peor, tambien
  // elegia mal en QUE cara imprimir: ver el bug de thicknessOnX mas abajo).
  // Arreglo: el pivote/rotacion del grupo que envuelve los labels es
  // EXACTAMENTE el mismo que usa DragBox.tsx internamente para rotar la
  // malla fisica (mismo centro de la envolvente, mismo angulo/eje) -asi el
  // texto queda en coordenadas LOCALES de esa misma caja y se mueve junto
  // con ella a cualquier angulo, positivo o negativo.
  const isTilted =
    !!piece.tilt_angle && piece.tilt_axis != null && piece.base_dx != null && piece.base_dy != null && piece.base_dz != null;
  const geomDx = isTilted ? piece.base_dx! : piece.dx;
  const geomDy = isTilted ? piece.base_dy! : piece.dy;
  const geomDz = isTilted ? piece.base_dz! : piece.dz;
  const pivotX = (pos.x + piece.dx / 2 - container.length / 2) * SCENE_SCALE;
  const pivotY = (pos.z + piece.dz / 2) * SCENE_SCALE;
  const pivotZ = (pos.y + piece.dy / 2 - container.width / 2) * SCENE_SCALE;
  const tiltAngleRad = isTilted ? (piece.tilt_angle! * Math.PI) / 180 : 0;
  const labelGroupRotation: [number, number, number] = isTilted
    ? piece.tilt_axis === "y"
      ? [tiltAngleRad, 0, 0]
      : [0, 0, tiltAngleRad]
    : [0, 0, 0];

  let color = dragOverride ? (dragOverride.valid ? VALID_COLOR : INVALID_COLOR) : selected ? SELECTED_COLOR : baseColor;
  if (!dragOverride && !selected) {
    if (visualState === "current") color = CURRENT_STEP_COLOR;
    else if (visualState === "past" && guideMode) color = GRAY_PAST_COLOR;
  }
  const edgeColor = dragOverride
    ? "#ffffff"
    : piece.locked
      ? LOCKED_EDGE_COLOR
      : selected
        ? "#ffffff"
        : "#1a1a1a";
  const isDimmedFuture = visualState === "future" && !dragOverride;
  const opacity = isDimmedFuture ? DIMMED_OPACITY : visualState === "past" && guideMode ? 0.5 : 0.82;

  function handlePointerDown(e: DragPointerEvent) {
    e.stopPropagation();
    onSelect(piece.id);
    if (piece.locked) return;
    onBeginDrag("move", piece.id, piece.x, piece.y, piece.z, piece.dx, piece.dy, piece.dz, piece.stackable, e.clientX, e.clientY);
  }

  // La cara del vidrio (Width x Height) es siempre la que forman dz (nunca
  // Thickness, por regla de orientacion) y el eje horizontal que NO es
  // Thickness. Segun la orientacion actual, ese eje horizontal puede ser dx
  // (P1-a/P2-a) o dy (P1-b/P2-b) — por eso no alcanza con asumir siempre dy:
  // si la pieza esta "girada" y Thickness quedo en dx, la etiqueta tiene que
  // moverse a la cara dx y centrarse en dy, o queda pegada a un borde de una
  // cara mucho mas ancha. El texto va "impreso" sobre esa cara (con un
  // pequeno margen hacia afuera) en vez de flotar separado de la pieza.
  //
  // Fase 5C-FINAL: usa geomDx/geomDy (dimensiones FISICAS) en vez de
  // piece.dx/piece.dy (la envolvente AABB, inflada por Tilt) -con la
  // envolvente, esta deteccion elegia la cara EQUIVOCADA en cuanto la pieza
  // se inclinaba (la inflacion cambia el valor numerico de dx/dy, dejan de
  // parecerse a source_thickness aunque Thickness siga siendo el mismo eje
  // fisico de siempre).
  const thicknessOnX = Math.abs(geomDx - piece.source_thickness) < Math.abs(geomDy - piece.source_thickness);

  // Todo lo que sigue esta en coordenadas LOCALES relativas al centro de la
  // caja (el origen del <group> de mas abajo, que ya tiene el pivote y la
  // rotacion de Tilt aplicados) -nunca coordenadas de mundo.
  const halfDx = (geomDx / 2) * SCENE_SCALE;
  const halfDy = (geomDy / 2) * SCENE_SCALE;
  const halfDz = (geomDz / 2) * SCENE_SCALE;
  const faceX = thicknessOnX ? halfDx + 0.004 : 0;
  const faceZ = thicknessOnX ? 0 : halfDy + 0.004;
  const faceTopY = halfDz - 0.05;
  const faceRotationY = thicknessOnX ? Math.PI / 2 : 0;
  const faceWidth = thicknessOnX ? geomDy : geomDx;

  // Tamano de fuente proporcional al tamano de la caja (legible tanto en
  // piezas grandes como chicas, sin volverse excesivo ni salirse de la cara),
  // con una jerarquia clara: Code (primario) > Description/Group (secundario).
  // El numero de secuencia tiene su propia jerarquia visual (ver mas abajo),
  // no comparte tamano/posicion con este bloque.
  const footprintSize = Math.min(faceWidth, geomDz) * SCENE_SCALE;
  const primaryFontSize = Math.min(0.15, Math.max(0.06, footprintSize * 0.2));
  const secondaryFontSize = primaryFontSize * 0.75;
  const maxWidth = faceWidth * SCENE_SCALE * 0.88;

  const secondaryLines = [piece.description, piece.group].filter(Boolean);
  const secondaryText = secondaryLines.join("\n");

  const { text: labelTextColor, outline: labelOutlineColor } = pickLabelColors(color);

  // Bloque de Code/Description/Group, apilado verticalmente desde el borde
  // superior de la cara.
  const primaryLineHeight = primaryFontSize * 1.3;
  const secondaryY = faceTopY - primaryLineHeight;

  // Numero de secuencia (Load/Unload Order): esquina INFERIOR-DERECHA de la
  // misma cara fija -nunca en el centro, nunca encima de Code/Description
  // (bug reportado: antes flotaba separado y se superponia). "Derecha" es un
  // offset perpendicular sobre el eje horizontal LOCAL de la cara (el mismo
  // que varia segun thicknessOnX), no una coordenada mundial fija, para que
  // quede en la misma esquina relativa sin importar la orientacion de la
  // pieza. Estilo fijo (blanco + outline negro grueso) en vez del contraste
  // dinamico: mas facil de reconocer de un vistazo y consistente entre Load
  // y Unload Order.
  const sequenceFontSize = Math.min(0.16, Math.max(0.07, footprintSize * 0.22));
  const faceHalfWidth = (faceWidth * SCENE_SCALE) / 2;
  const badgeMargin = Math.min(faceHalfWidth * 0.5, footprintSize * 0.12);
  const badgeInset = faceHalfWidth - badgeMargin;
  const badgeX = thicknessOnX ? faceX : faceX + badgeInset;
  const badgeZ = thicknessOnX ? faceZ + badgeInset : faceZ;
  const badgeY = -halfDz + Math.min(geomDz * SCENE_SCALE * 0.15, 0.09);

  const showInfo = showLabels && !isDimmedFuture;
  const showSequenceBadge = sequenceLabel !== null && !isDimmedFuture;

  return (
    <>
      <LoadUnitBody
        itemType={piece.item_type}
        x={pos.x}
        y={pos.y}
        z={pos.z}
        dx={piece.dx}
        dy={piece.dy}
        dz={piece.dz}
        container={container}
        color={color}
        edgeColor={edgeColor}
        opacity={opacity}
        onPointerDown={handlePointerDown}
        tiltAngleDeg={piece.tilt_angle}
        tiltAxis={piece.tilt_axis}
        baseDx={piece.base_dx}
        baseDy={piece.base_dy}
        baseDz={piece.base_dz}
      />
      <group position={[pivotX, pivotY, pivotZ]} rotation={labelGroupRotation}>
      {showInfo && (
        <Text
          position={[faceX, faceTopY, faceZ]}
          rotation={[0, faceRotationY, 0]}
          fontSize={primaryFontSize}
          color={labelTextColor}
          anchorX="center"
          anchorY="top"
          maxWidth={maxWidth}
          overflowWrap="break-word"
          outlineWidth={0.006}
          outlineColor={labelOutlineColor}
        >
          {piece.code}
        </Text>
      )}
      {showInfo && secondaryText && (
        <Text
          position={[faceX, secondaryY, faceZ]}
          rotation={[0, faceRotationY, 0]}
          fontSize={secondaryFontSize}
          color={labelTextColor}
          anchorX="center"
          anchorY="top"
          lineHeight={1.2}
          maxWidth={maxWidth}
          overflowWrap="break-word"
          outlineWidth={0.005}
          outlineColor={labelOutlineColor}
        >
          {secondaryText}
        </Text>
      )}
      {showSequenceBadge && (
        <Text
          position={[badgeX, badgeY, badgeZ]}
          rotation={[0, faceRotationY, 0]}
          fontSize={sequenceFontSize}
          color="#ffffff"
          anchorX="right"
          anchorY="bottom"
          fontWeight="bold"
          outlineWidth={0.01}
          outlineColor="#000000"
        >
          {`${sequenceLabel}`}
        </Text>
      )}
      </group>
    </>
  );
}
