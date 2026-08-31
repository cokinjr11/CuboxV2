/**
 * Espejo en TypeScript de backend/app/core/geometry.py (boxes_overlap,
 * within_container, check_support), mas findRestingZ (sin equivalente
 * backend: solo sirve para decidir la altura candidata durante un drag).
 *
 * Uso exclusivo: feedback visual en tiempo real mientras se arrastra una
 * pieza en la vista 3D. NO es la fuente de verdad — el backend vuelve a
 * validar con core/manual_move.validate_placement en cada commit
 * (apply-move / insert-piece / rotate-piece). Si cambian las reglas en
 * geometry.py, replicar el cambio aqui.
 */

export interface Box {
  id: string;
  x: number;
  y: number;
  z: number;
  dx: number;
  dy: number;
  dz: number;
  stackable: boolean;
}

const TOL = 1e-6;

export function boxesOverlap(a: Box, b: Box, tol = TOL): boolean {
  if (a.x + a.dx <= b.x + tol || b.x + b.dx <= a.x + tol) return false;
  if (a.y + a.dy <= b.y + tol || b.y + b.dy <= a.y + tol) return false;
  if (a.z + a.dz <= b.z + tol || b.z + b.dz <= a.z + tol) return false;
  return true;
}

export function withinContainer(
  box: Box,
  length: number,
  width: number,
  height: number,
  tol = TOL
): boolean {
  if (box.x < -tol || box.y < -tol || box.z < -tol) return false;
  if (box.x + box.dx > length + tol) return false;
  if (box.y + box.dy > width + tol) return false;
  if (box.z + box.dz > height + tol) return false;
  return true;
}

export function hasCollision(box: Box, others: Box[], tol = TOL): Box | null {
  for (const other of others) {
    if (other.id === box.id) continue;
    if (boxesOverlap(box, other, tol)) return other;
  }
  return null;
}

const MIN_SUPPORT_PCT = 0.8;

function xyOverlapArea(a: Box, b: Box): number {
  const ox = Math.max(0, Math.min(a.x + a.dx, b.x + b.dx) - Math.max(a.x, b.x));
  const oy = Math.max(0, Math.min(a.y + a.dy, b.y + b.dy) - Math.max(a.y, b.y));
  return ox * oy;
}

/**
 * Espejo de check_support en geometry.py: soporte MULTIPLE por porcentaje.
 * Una pieza puede apoyarse en varias piezas apilables a la vez (por ejemplo,
 * dos ventanas juntas cuya suma de largo iguala el largo de la pieza de
 * arriba) - no hace falta que UNA sola contenga toda la base, alcanza con que
 * el area de solape combinada cubra al menos MIN_SUPPORT_PCT de la base.
 */
export function checkSupport(box: Box, others: Box[], tol = TOL): { ok: boolean; reason: string } {
  if (box.z <= tol) return { ok: true, reason: "" };

  const touching = others.filter(
    (o) => o.id !== box.id && Math.abs(o.z + o.dz - box.z) < tol && xyOverlapArea(box, o) > tol
  );

  if (touching.length === 0) return { ok: false, reason: "Pieza flotando: no hay soporte debajo" };

  const nonStackable = touching.find((o) => !o.stackable);
  if (nonStackable) {
    return { ok: false, reason: `No se puede apilar sobre la pieza no apilable ${nonStackable.id}` };
  }

  const baseArea = box.dx * box.dy;
  const supportedArea = touching.reduce((sum, o) => sum + xyOverlapArea(box, o), 0);
  const supportPct = baseArea > tol ? supportedArea / baseArea : 0;

  if (supportPct < MIN_SUPPORT_PCT - tol) {
    const pctDisplay = Math.round(supportPct * 1000) / 10;
    return {
      ok: false,
      reason: `Soporte insuficiente: ${pctDisplay}% de la base apoyada (minimo ${MIN_SUPPORT_PCT * 100}%)`,
    };
  }

  return { ok: true, reason: "" };
}

export interface PlacementCheck {
  x: number;
  y: number;
  z: number;
  valid: boolean;
}

/**
 * Dada una posicion XY candidata (piso del contenedor) y el tamano fijo de la
 * pieza que se arrastra (dx, dy, dz — la orientacion no cambia durante un
 * drag), busca la altura Z mas alta valida: el piso, o la parte superior de
 * alguna pieza apilable cuya huella contenga completamente la huella
 * candidata. Prueba los niveles de mayor a menor para preferir apilar sobre
 * la pieza mas alta disponible bajo el cursor.
 */
export function findRestingZ(
  x: number,
  y: number,
  dx: number,
  dy: number,
  dz: number,
  stackable: boolean,
  pieceId: string,
  others: Box[],
  container: { length: number; width: number; height: number }
): PlacementCheck {
  const overlapsXY = (o: Box) => x < o.x + o.dx - TOL && o.x < x + dx - TOL && y < o.y + o.dy - TOL && o.y < y + dy - TOL;

  const levels = new Set<number>([0]);
  for (const o of others) {
    if (o.id !== pieceId && overlapsXY(o)) levels.add(o.z + o.dz);
  }

  const sortedLevels = Array.from(levels).sort((a, b) => b - a);

  for (const z of sortedLevels) {
    const candidate: Box = { id: pieceId, x, y, z, dx, dy, dz, stackable };
    if (!withinContainer(candidate, container.length, container.width, container.height)) continue;
    if (hasCollision(candidate, others)) continue;
    const support = checkSupport(candidate, others);
    if (!support.ok) continue;
    return { x, y, z, valid: true };
  }

  // Ningun nivel valido: se muestra en el piso, marcado como invalido, para
  // que el usuario vea donde esta chocando.
  return { x, y, z: 0, valid: false };
}
