import { Edges } from "@react-three/drei";
import { SCENE_SCALE } from "../config";

export interface DragPointerEvent {
  clientX: number;
  clientY: number;
  stopPropagation: () => void;
}

interface Props {
  /** Posicion de la esquina minima de la caja, en mm (coordenadas backend). */
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

/** Caja de presentacion pura: convierte coordenadas backend (mm, esquina) a
 * unidades de three.js (centro). La usan PieceMesh (pieza normal / en
 * arrastre) y el fantasma de insercion, para no duplicar esta conversion. */
export function DragBox({ x, y, z, dx, dy, dz, container, color, edgeColor, opacity = 0.82, onPointerDown }: Props) {
  const cx = (x + dx / 2 - container.length / 2) * SCENE_SCALE;
  const cy = (z + dz / 2) * SCENE_SCALE;
  const cz = (y + dy / 2 - container.width / 2) * SCENE_SCALE;

  return (
    <mesh position={[cx, cy, cz]} onPointerDown={onPointerDown}>
      <boxGeometry args={[dx * SCENE_SCALE, dz * SCENE_SCALE, dy * SCENE_SCALE]} />
      <meshStandardMaterial color={color} transparent opacity={opacity} />
      <Edges color={edgeColor} />
    </mesh>
  );
}
