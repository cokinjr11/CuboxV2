"""Integracion NAGSA, A2 (H3): precondicion de Optimize Remaining. Unica
responsabilidad: verificar que las piezas Locked sean un punto de partida
fisicamente valido (dentro del Load Space, sin colisionar entre si, sin
exceder el payload). Antes estaba inline en api/routes.py:optimize_remaining.

Reutiliza los validadores canonicos de core/geometry.py (fuente unica de
verdad fisica) -no reimplementa ninguna regla."""

from __future__ import annotations

from app.core.geometry import Box, boxes_overlap, within_container
from app.models.schemas import PlacedPiece
from app.services.errors import conflict
from app.services.plan_state import PlanState

TOL = 1e-6


def locked_pieces(state: PlanState) -> list[PlacedPiece]:
    return [p for p in state.placed if p.locked]


def validate_locked_pieces(state: PlanState) -> None:
    """Levanta PlanOpError(CONFLICT) con el mismo mensaje de siempre ante el
    primer problema; no devuelve nada si todo esta bien."""
    load_space = state.load_space
    locked = locked_pieces(state)

    locked_boxes = [
        Box(p.id, p.x, p.y, p.z, p.dx, p.dy, p.dz, p.stackable, p.max_stack_weight) for p in locked
    ]
    for i, a in enumerate(locked_boxes):
        if not within_container(a, load_space.length, load_space.width, load_space.height):
            raise conflict(f"La pieza bloqueada {a.id} esta fuera de los limites del contenedor")
        for b in locked_boxes[i + 1 :]:
            if boxes_overlap(a, b):
                raise conflict(f"Las piezas bloqueadas {a.id} y {b.id} colisionan entre si")

    locked_weight = sum(p.weight for p in locked)
    if locked_weight > load_space.max_weight + TOL:
        raise conflict("Las piezas bloqueadas ya exceden el peso maximo del contenedor")
