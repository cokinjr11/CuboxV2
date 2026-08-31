"""
Funciones geometricas compartidas: colisiones (AABB), limites del contenedor,
soporte para apilamiento (por porcentaje), separacion minima (clearance) y
peso maximo apilado.

Usadas tanto por el algoritmo automatico de cubicaje como por la validacion
de edicion manual, para garantizar que ambos apliquen exactamente las mismas
reglas.
"""

from dataclasses import dataclass

TOL = 1e-6

MIN_SUPPORT_PCT = 0.8
"""Porcentaje minimo del area de la base que debe estar apoyada para que una
pieza apilada se considere valida (no solo apoyada en una esquina)."""


@dataclass
class Box:
    """Caja axis-aligned que representa una pieza colocada en el contenedor."""

    id: str
    x: float
    y: float
    z: float
    dx: float
    dy: float
    dz: float
    stackable: bool = True
    max_stack_weight: float | None = None

    @property
    def top_z(self) -> float:
        return self.z + self.dz

    @property
    def max_x(self) -> float:
        return self.x + self.dx

    @property
    def max_y(self) -> float:
        return self.y + self.dy

    @property
    def base_area(self) -> float:
        return self.dx * self.dy


def boxes_overlap(a: Box, b: Box, tol: float = TOL) -> bool:
    """True si dos cajas se traslapan en volumen (tocarse en una cara no cuenta)."""
    if a.x + a.dx <= b.x + tol or b.x + b.dx <= a.x + tol:
        return False
    if a.y + a.dy <= b.y + tol or b.y + b.dy <= a.y + tol:
        return False
    if a.z + a.dz <= b.z + tol or b.z + b.dz <= a.z + tol:
        return False
    return True


def boxes_too_close(a: Box, b: Box, clearance: float, tol: float = TOL) -> bool:
    """True si `a` y `b` violan la separacion minima (clearance) entre piezas.

    El clearance solo aplica cuando las dos piezas comparten rango Z (estan al
    mismo nivel, una al lado de la otra) - no separa una pieza de la que tiene
    apoyada debajo o encima, eso lo regula check_support. No modifica las
    dimensiones reales de las piezas, solo se usa para esta validacion.
    """
    if clearance <= tol:
        return False
    if a.z + a.dz <= b.z + tol or b.z + b.dz <= a.z + tol:
        return False  # no comparten nivel Z: el clearance lateral no aplica

    gap_x = max(b.x - (a.x + a.dx), a.x - (b.x + b.dx))
    gap_y = max(b.y - (a.y + a.dy), a.y - (b.y + b.dy))

    if gap_x < -tol and gap_y < -tol:
        return True  # ya se solapan en XY (esto ademas seria una colision)
    # Si se solapan en un eje, la separacion real es la del otro eje.
    if gap_x < -tol:
        return gap_y < clearance - tol
    if gap_y < -tol:
        return gap_x < clearance - tol
    # No se solapan en ningun eje (piezas en diagonal): distancia entre esquinas.
    diagonal_gap = (gap_x**2 + gap_y**2) ** 0.5
    return diagonal_gap < clearance - tol


def within_container(box: Box, length: float, width: float, height: float, tol: float = TOL) -> bool:
    """True si la caja cabe completamente dentro de los limites del contenedor."""
    if box.x < -tol or box.y < -tol or box.z < -tol:
        return False
    if box.x + box.dx > length + tol:
        return False
    if box.y + box.dy > width + tol:
        return False
    if box.z + box.dz > height + tol:
        return False
    return True


def has_collision(box: Box, others: list[Box], tol: float = TOL) -> Box | None:
    """Devuelve la primera caja con la que colisiona `box`, o None si no hay colision."""
    for other in others:
        if other.id == box.id:
            continue
        if boxes_overlap(box, other, tol):
            return other
    return None


def has_clearance_conflict(box: Box, others: list[Box], clearance: float, tol: float = TOL) -> Box | None:
    """Devuelve la primera caja que viola el clearance con `box`, o None."""
    if clearance <= tol:
        return None
    for other in others:
        if other.id == box.id:
            continue
        if boxes_too_close(box, other, clearance, tol):
            return other
    return None


def _xy_overlap_area(a: Box, b: Box) -> float:
    ox = max(0.0, min(a.x + a.dx, b.x + b.dx) - max(a.x, b.x))
    oy = max(0.0, min(a.y + a.dy, b.y + b.dy) - max(a.y, b.y))
    return ox * oy


def check_support(box: Box, others: list[Box], tol: float = TOL, min_support_pct: float = MIN_SUPPORT_PCT) -> tuple[bool, str]:
    """Valida que `box` este correctamente soportada.

    - Si descansa en el piso (z ~ 0): valido.
    - Si descansa sobre una o mas piezas: todas deben ser stackable=True, y la
      suma del area de solape en XY con esas piezas (soporte multiple) debe
      cubrir al menos `min_support_pct` del area de la base de `box` (regla
      conservadora: no basta apoyarse en una esquina).
    - Si no hay ninguna pieza debajo tocando su z: invalido (pieza flotando).
    """
    if box.z <= tol:
        return True, ""

    touching = [o for o in others if o.id != box.id and abs(o.top_z - box.z) < tol and _xy_overlap_area(box, o) > tol]

    if not touching:
        return False, "Pieza flotando: no hay soporte debajo"

    non_stackable = [o for o in touching if not o.stackable]
    if non_stackable:
        return False, f"No se puede apilar sobre la pieza no apilable {non_stackable[0].id}"

    supported_area = sum(_xy_overlap_area(box, o) for o in touching)
    support_pct = supported_area / box.base_area if box.base_area > tol else 0.0

    if support_pct < min_support_pct - tol:
        pct_display = round(support_pct * 100, 1)
        return False, f"Soporte insuficiente: {pct_display}% de la base apoyada (minimo {min_support_pct * 100:.0f}%)"

    return True, ""


def check_stack_weight(
    box: Box, box_weight: float, others: list[Box], weights_by_id: dict[str, float], tol: float = TOL
) -> tuple[bool, str]:
    """Valida MaxStackWeight: la suma de pesos que descansan DIRECTAMENTE sobre
    una pieza no puede exceder su max_stack_weight (si tiene uno definido).
    Chequeo directo, no transitivo (no seguimos apilamientos de varios niveles).
    """
    supporters = [o for o in others if o.id != box.id and abs(o.top_z - box.z) < tol and _xy_overlap_area(box, o) > tol]

    for s in supporters:
        if s.max_stack_weight is None:
            continue
        already_on_top = sum(
            weights_by_id.get(o.id, 0.0)
            for o in others
            if o.id != box.id and o.id != s.id and abs(o.z - s.top_z) < tol and _xy_overlap_area(s, o) > tol
        )
        if already_on_top + box_weight > s.max_stack_weight + tol:
            return False, f"Excede el peso maximo apilable sobre {s.id} ({s.max_stack_weight} kg)"

    return True, ""
