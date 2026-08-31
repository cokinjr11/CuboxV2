"""Zonas reservadas: volumenes del contenedor donde no se puede colocar nada.

Concepto generico y reutilizable (no solo para el pasillo central) para que
en el futuro se puedan agregar otras zonas prohibidas sin cambiar la logica
de validacion.
"""

from dataclasses import dataclass

from app.core.geometry import TOL, Box
from app.models.schemas import ContainerSpec


@dataclass(frozen=True)
class ReservedZone:
    x: float
    y: float
    z: float
    length: float  # extension en el eje X del contenedor
    width: float  # extension en el eje Y del contenedor
    height: float  # extension en el eje Z del contenedor
    label: str = "reserved"


def _zone_overlaps_box(zone: ReservedZone, box: Box, tol: float = TOL) -> bool:
    if box.x + box.dx <= zone.x + tol or zone.x + zone.length <= box.x + tol:
        return False
    if box.y + box.dy <= zone.y + tol or zone.y + zone.width <= box.y + tol:
        return False
    if box.z + box.dz <= zone.z + tol or zone.z + zone.height <= box.z + tol:
        return False
    return True


def zone_conflict(box: Box, zones: list[ReservedZone], tol: float = TOL) -> ReservedZone | None:
    """Devuelve la primera zona reservada que invade `box`, o None."""
    for zone in zones:
        if _zone_overlaps_box(zone, box, tol):
            return zone
    return None


def zone_conflict_with_clearance(box: Box, zones: list[ReservedZone], clearance: float, tol: float = TOL) -> ReservedZone | None:
    """Como zone_conflict, pero exige ademas el mismo Minimum Clearance que ya
    se respeta entre piezas: una pieza no puede quedar pegada (gap=0) al
    borde de una zona reservada (p.ej. el pasillo central) si hay clearance
    configurado. Se implementa inflando cada zona por `clearance` en todos
    los ejes -mismo resultado que exigir un hueco de esa distancia en el
    borde, sin duplicar la logica de boxes_too_close para geometria AABB."""
    if clearance <= tol:
        return zone_conflict(box, zones, tol)
    inflated = [
        ReservedZone(
            x=z.x - clearance,
            y=z.y - clearance,
            z=z.z,
            length=z.length + 2 * clearance,
            width=z.width + 2 * clearance,
            height=z.height,
            label=z.label,
        )
        for z in zones
    ]
    conflict = zone_conflict(box, inflated, tol)
    if conflict is None:
        return None
    # Devolver la zona ORIGINAL (no la inflada) para que el mensaje/label sea
    # el real, no una geometria auxiliar interna.
    return next(z for z in zones if z.label == conflict.label)


def central_aisle_zone(container: ContainerSpec, aisle_width_mm: float) -> ReservedZone:
    """Pasillo central: recorre todo el largo, toda la altura, centrado en el
    ancho del contenedor."""
    aisle_width_mm = min(aisle_width_mm, container.width)
    y = (container.width - aisle_width_mm) / 2
    return ReservedZone(
        x=0,
        y=y,
        z=0,
        length=container.length,
        width=aisle_width_mm,
        height=container.height,
        label="central_aisle",
    )
