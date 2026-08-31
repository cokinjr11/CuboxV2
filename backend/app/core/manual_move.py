"""Validacion de edicion manual (mover, insertar, rotar) sobre el cubicaje.

Reutiliza exactamente las mismas reglas del algoritmo automatico
(orientation.py y geometry.py) para que el usuario nunca pueda, editando a
mano, violar ninguna restriccion que el algoritmo respeta. `validate_placement`
es la unica fuente de verdad: se usa tanto para mover una pieza ya colocada
como para insertar una pieza nueva desde Unloaded Items o para rotarla.
"""

from app.core.geometry import (
    Box,
    check_stack_weight,
    check_support,
    has_clearance_conflict,
    has_collision,
    within_container,
)
from app.core.orientation import is_valid_orientation, orientation_rejection_reason
from app.core.reserved_zones import ReservedZone, zone_conflict_with_clearance
from app.models.schemas import ContainerSpec, OrientationPolicy, PlacedPiece


def validate_placement(
    piece_id: str,
    width: float,
    height: float,
    thickness: float,
    stackable: bool,
    weight: float,
    max_stack_weight: float | None,
    x: float,
    y: float,
    z: float,
    dx: float,
    dy: float,
    dz: float,
    other_pieces: list[PlacedPiece],
    container: ContainerSpec,
    reserved_zones: list[ReservedZone] | None = None,
    clearance: float = 0.0,
    orientation_policy: OrientationPolicy = OrientationPolicy.PANEL_EDGE_ONLY,
) -> tuple[bool, str]:
    reserved_zones = reserved_zones or []

    if not is_valid_orientation(width, height, thickness, dx, dy, dz, orientation_policy):
        reason = orientation_rejection_reason(orientation_policy)
        return False, reason[0].upper() + reason[1:]

    candidate_box = Box(
        id=piece_id, x=x, y=y, z=z, dx=dx, dy=dy, dz=dz, stackable=stackable, max_stack_weight=max_stack_weight
    )

    if not within_container(candidate_box, container.length, container.width, container.height):
        return False, "La pieza quedaria fuera de los limites del contenedor"

    zone = zone_conflict_with_clearance(candidate_box, reserved_zones, clearance)
    if zone is not None:
        return False, f"La pieza invade una zona reservada ({zone.label})"

    other_boxes = [
        Box(
            id=p.id,
            x=p.x,
            y=p.y,
            z=p.z,
            dx=p.dx,
            dy=p.dy,
            dz=p.dz,
            stackable=p.stackable,
            max_stack_weight=p.max_stack_weight,
        )
        for p in other_pieces
        if p.id != piece_id
    ]

    collision = has_collision(candidate_box, other_boxes)
    if collision is not None:
        return False, f"Colisiona con la pieza {collision.id}"

    clearance_conflict = has_clearance_conflict(candidate_box, other_boxes, clearance)
    if clearance_conflict is not None:
        return False, f"No respeta la separacion minima con la pieza {clearance_conflict.id}"

    ok, reason = check_support(candidate_box, other_boxes)
    if not ok:
        return False, reason

    weights_by_id = {p.id: p.weight for p in other_pieces if p.id != piece_id}
    ok, reason = check_stack_weight(candidate_box, weight, other_boxes, weights_by_id)
    if not ok:
        return False, reason

    return True, ""


def validate_move(
    piece: PlacedPiece,
    new_x: float,
    new_y: float,
    new_z: float,
    new_dx: float,
    new_dy: float,
    new_dz: float,
    other_pieces: list[PlacedPiece],
    container: ContainerSpec,
    reserved_zones: list[ReservedZone] | None = None,
    clearance: float = 0.0,
) -> tuple[bool, str]:
    return validate_placement(
        piece.id,
        piece.source_width,
        piece.source_height,
        piece.source_thickness,
        piece.stackable,
        piece.weight,
        piece.max_stack_weight,
        new_x,
        new_y,
        new_z,
        new_dx,
        new_dy,
        new_dz,
        other_pieces,
        container,
        reserved_zones,
        clearance,
        piece.resolved_orientation_policy,
    )
