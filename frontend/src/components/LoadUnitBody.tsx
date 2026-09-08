import { DragBox, type DragPointerEvent } from "./DragBox";
import { PalletBody } from "./PalletBody";
import type { ItemType } from "../types";

interface Props {
  itemType: ItemType | undefined;
  x: number;
  y: number;
  z: number;
  dx: number;
  dy: number;
  dz: number;
  container: { length: number; width: number };
  color: string;
  edgeColor: string;
  opacity?: number;
  onPointerDown?: (e: DragPointerEvent) => void;
  /** Fase 5C: ver DragBox.tsx -solo se usan para item_type="panel" (unico
   * tipo con Tilt en esta fase); PalletBody no las necesita. */
  tiltAngleDeg?: number;
  tiltAxis?: "x" | "y" | null;
  baseDx?: number | null;
  baseDy?: number | null;
  baseDz?: number | null;
}

/** Punto unico de despacho de la geometria 3D de un Load Unit segun su
 * ItemType (Fase 6.1). Agregar un futuro tipo con visual propio (Drum,
 * Sack, ...) solo requiere un nuevo caso aca -PieceMesh/Scene3D, seleccion,
 * drag, labels y secuencia siguen identicos para todos los tipos. */
export function LoadUnitBody({ itemType, tiltAngleDeg, tiltAxis, baseDx, baseDy, baseDz, ...rest }: Props) {
  if (itemType === "pallet") return <PalletBody {...rest} />;
  return <DragBox {...rest} tiltAngleDeg={tiltAngleDeg} tiltAxis={tiltAxis} baseDx={baseDx} baseDy={baseDy} baseDz={baseDz} />;
}
