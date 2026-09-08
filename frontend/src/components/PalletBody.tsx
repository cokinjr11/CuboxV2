import { Edges } from "@react-three/drei";
import { SCENE_SCALE } from "../config";
import type { DragPointerEvent } from "./DragBox";

const PALLET_BASE_COLOR = "#7a5230";
const MIN_BASE_HEIGHT_MM = 60;
const BASE_HEIGHT_FRACTION = 0.08;
// Tope MAS BAJO que antes (era 0.35): para piezas muy bajas (datos de
// prueba tipo caja chica importados como PALLET, altura real < ~400mm) el
// tope viejo hacia que la base ocupara hasta 1/3 de la altura total -se
// veia como "2 bloques apilados" en vez de una base delgada. 0.15 mantiene
// la base SIEMPRE proporcionalmente fina sin importar que tan corta sea la
// pieza (un pallet real bien cargado igual queda con base ~8%, ver abajo).
const MAX_BASE_FRACTION = 0.15;

interface Props {
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
}

/** Representacion 3D de un PALLET (Fase 6.1): el mismo bounding box que ya
 * usa DragBox, dividido en 2 bandas apiladas -una base delgada (color
 * madera fijo, no afectado por Color By) y el bulto de carga arriba, con el
 * mismo color/opacity que tendria una pieza generica. NO agrega altura ni
 * cambia collision/packing: dz total (base + carga) es exactamente el
 * mismo que ya usa el packer -ver seccion 2 y 10 del pedido de Fase 6.1.
 * Ambas bandas comparten el mismo onPointerDown para que clickear/arrastrar
 * cualquiera de las 2 seleccione/mueva la MISMA pieza. */
export function PalletBody({ x, y, z, dx, dy, dz, container, color, edgeColor, opacity = 0.82, onPointerDown }: Props) {
  const baseHeight = Math.min(Math.max(MIN_BASE_HEIGHT_MM, dz * BASE_HEIGHT_FRACTION), dz * MAX_BASE_FRACTION);
  const loadHeight = dz - baseHeight;

  const cx = (x + dx / 2 - container.length / 2) * SCENE_SCALE;
  const cz = (y + dy / 2 - container.width / 2) * SCENE_SCALE;
  const cyBase = (z + baseHeight / 2) * SCENE_SCALE;
  const cyLoad = (z + baseHeight + loadHeight / 2) * SCENE_SCALE;

  return (
    <>
      <mesh position={[cx, cyBase, cz]} onPointerDown={onPointerDown}>
        <boxGeometry args={[dx * SCENE_SCALE, baseHeight * SCENE_SCALE, dy * SCENE_SCALE]} />
        <meshStandardMaterial color={PALLET_BASE_COLOR} transparent opacity={Math.min(1, opacity + 0.1)} />
        <Edges color={edgeColor} />
      </mesh>
      <mesh position={[cx, cyLoad, cz]} onPointerDown={onPointerDown}>
        <boxGeometry args={[dx * SCENE_SCALE, loadHeight * SCENE_SCALE, dy * SCENE_SCALE]} />
        <meshStandardMaterial color={color} transparent opacity={opacity} />
        <Edges color={edgeColor} />
      </mesh>
    </>
  );
}
