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
  /** Fase 5C (Tilt/Inclination): si la pieza esta inclinada, `dx/dy/dz`
   * arriba son la ENVOLVENTE AABB (usada por collision/boundaries), no la
   * geometria fisica real -ver backend app/core/orientation.py:apply_tilt.
   * Con estos props presentes, se renderiza la caja con sus dimensiones
   * fisicas reales (baseDx/baseDy/baseDz) rotada `tiltAngleDeg` grados
   * alrededor de su PROPIO centro. La formula de apply_tilt es exactamente
   * la envolvente de un rectangulo rotado sobre su centro (new_w = w cos +
   * h sin, etc.) -por eso el centro de la envolvente (cx/cy/cz, sin
   * cambios abajo) coincide EXACTAMENTE con el pivote de rotacion: no hace
   * falta ningun offset extra para que la pieza rotada quede apoyada en el
   * piso y dentro de su propia envolvente. El angulo renderizado es el
   * mismo `tilt_angle` que valida el backend (ver PieceMesh.tsx) -nunca
   * una rotacion solo cosmetica (seccion 31 del pedido). */
  tiltAngleDeg?: number;
  tiltAxis?: "x" | "y" | null;
  baseDx?: number | null;
  baseDy?: number | null;
  baseDz?: number | null;
}

/** Caja de presentacion pura: convierte coordenadas backend (mm, esquina) a
 * unidades de three.js (centro). La usan PieceMesh (pieza normal / en
 * arrastre) y el fantasma de insercion, para no duplicar esta conversion. */
export function DragBox({
  x,
  y,
  z,
  dx,
  dy,
  dz,
  container,
  color,
  edgeColor,
  opacity = 0.82,
  onPointerDown,
  tiltAngleDeg,
  tiltAxis,
  baseDx,
  baseDy,
  baseDz,
}: Props) {
  const cx = (x + dx / 2 - container.length / 2) * SCENE_SCALE;
  const cy = (z + dz / 2) * SCENE_SCALE;
  const cz = (y + dy / 2 - container.width / 2) * SCENE_SCALE;

  const isTilted =
    !!tiltAngleDeg && tiltAxis != null && baseDx != null && baseDy != null && baseDz != null;
  const geomDx = isTilted ? baseDx! : dx;
  const geomDy = isTilted ? baseDy! : dy;
  const geomDz = isTilted ? baseDz! : dz;
  // Mapeo de ejes (ver comentario arriba): world (y, z) -> three.js (z, y),
  // inclinar sobre tilt_axis="y" rota alrededor del eje X de three.js;
  // world (x, z) -> three.js (x, y), tilt_axis="x" rota alrededor del eje Z.
  const angleRad = isTilted ? (tiltAngleDeg! * Math.PI) / 180 : 0;
  const rotation: [number, number, number] = isTilted
    ? tiltAxis === "y"
      ? [angleRad, 0, 0]
      : [0, 0, angleRad]
    : [0, 0, 0];

  return (
    <mesh position={[cx, cy, cz]} rotation={rotation} onPointerDown={onPointerDown}>
      <boxGeometry args={[geomDx * SCENE_SCALE, geomDz * SCENE_SCALE, geomDy * SCENE_SCALE]} />
      <meshStandardMaterial color={color} transparent opacity={opacity} />
      <Edges color={edgeColor} />
    </mesh>
  );
}
