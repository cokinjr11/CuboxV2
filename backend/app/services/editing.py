"""Integracion NAGSA, A2 (H4, H5): edicion manual de un plan. Unica
responsabilidad: aplicar UNA edicion (si es valida) y devolver el estado
nuevo.

Cada funcion recibe un PlanState y devuelve OTRO (copia profunda): nunca
muta el de entrada. NO guarda historial de undo/redo (eso es de la sesion
local, local/session.py) y NO recalcula derivados (services/derivation.py).
Antes cada endpoint de api/routes.py hacia las cuatro cosas juntas.

Toda la validacion fisica sigue viviendo en core/manual_move.py -la misma
que usa el optimizador automatico: el usuario no puede violar a mano nada
que el optimizador respete."""

from __future__ import annotations

from app.core.manual_move import validate_move as _validate_move
from app.core.manual_move import validate_placement, validate_tilt_change
from app.core.orientation import get_valid_orientations, toggle_orientation, turn_orientation
from app.models.schemas import (
    InsertPieceRequest,
    MoveRequest,
    MoveValidationResult,
    PlacedPiece,
    SetTiltRequest,
)
from app.services.errors import conflict, not_found
from app.services.piece_mapping import placed_to_unloaded, unloaded_to_placed
from app.services.plan_state import PlanState, reserved_zones_from_state


def _copy(state: PlanState) -> PlanState:
    return state.model_copy(deep=True)


def find_placed(state: PlanState, piece_id: str) -> PlacedPiece:
    piece = next((p for p in state.placed if p.id == piece_id), None)
    if piece is None:
        raise not_found(f"Pieza {piece_id} no encontrada en el cubicaje actual")
    return piece


def _ensure_unlocked(piece: PlacedPiece) -> None:
    if piece.locked:
        raise conflict(f"La pieza {piece.id} esta bloqueada (Locked). Desbloqueala primero.")


def _check_move(state: PlanState, piece: PlacedPiece, move: MoveRequest) -> tuple[bool, str]:
    return _validate_move(
        piece,
        move.x,
        move.y,
        move.z,
        move.dx,
        move.dy,
        move.dz,
        state.placed,
        state.load_space,
        reserved_zones_from_state(state.reserved_zones),
        state.clearance,
    )


def validate_move(state: PlanState, move: MoveRequest) -> MoveValidationResult:
    """Solo pregunta -no aplica nada (ni exige que la pieza este Unlocked,
    igual que siempre hizo /api/validate-move)."""
    valid, reason = _check_move(state, find_placed(state, move.piece_id), move)
    return MoveValidationResult(valid=valid, reason=reason)


def move_piece(state: PlanState, move: MoveRequest) -> PlanState:
    new = _copy(state)
    piece = find_placed(new, move.piece_id)
    _ensure_unlocked(piece)

    valid, reason = _check_move(new, piece, move)
    if not valid:
        raise conflict(reason)

    piece.x, piece.y, piece.z = move.x, move.y, move.z
    piece.dx, piece.dy, piece.dz = move.dx, move.dy, move.dz
    return new


def remove_piece(state: PlanState, piece_id: str) -> PlanState:
    new = _copy(state)
    piece = find_placed(new, piece_id)
    _ensure_unlocked(piece)

    new.placed = [p for p in new.placed if p.id != piece.id]
    new.unloaded.append(placed_to_unloaded(piece))
    return new


def insert_piece(state: PlanState, req: InsertPieceRequest) -> PlanState:
    new = _copy(state)

    item = next((u for u in new.unloaded if u.id == req.unloaded_id), None)
    if item is None:
        raise not_found(f"Pieza {req.unloaded_id} no encontrada en Unloaded Items")

    matching = next(
        (
            o
            for o in get_valid_orientations(
                item.dimensions,
                item.resolved_orientation_policy,
                allow_tilt=item.allow_tilt,
                max_tilt_angle=item.max_tilt_angle or 0.0,
            )
            if o.dx == req.dx and o.dy == req.dy and o.dz == req.dz
        ),
        None,
    )
    orientation_label = matching.label if matching else ""
    if matching is not None:
        insert_tilt_angle = matching.tilt_angle
        insert_tilt_axis = matching.tilt_axis
        insert_base_dx = matching.base_dx if matching.base_dx is not None else matching.dx
        insert_base_dy = matching.base_dy if matching.base_dy is not None else matching.dy
        insert_base_dz = matching.base_dz if matching.base_dz is not None else matching.dz
    else:
        insert_tilt_angle, insert_tilt_axis = 0.0, None
        insert_base_dx = insert_base_dy = insert_base_dz = None

    valid, reason = validate_placement(
        item.id,
        item.width,
        item.height,
        item.thickness,
        item.stackable,
        item.weight,
        item.max_stack_weight,
        req.x,
        req.y,
        req.z,
        req.dx,
        req.dy,
        req.dz,
        new.placed,
        new.load_space,
        reserved_zones_from_state(new.reserved_zones),
        new.clearance,
        item.resolved_orientation_policy,
        allow_tilt=item.allow_tilt,
        max_tilt_angle=item.max_tilt_angle or 0.0,
        tilt_angle=insert_tilt_angle,
        item_type=item.item_type,
        tilt_axis=insert_tilt_axis,
        base_dx=insert_base_dx,
        base_dy=insert_base_dy,
        base_dz=insert_base_dz,
    )
    if not valid:
        raise conflict(reason)

    new.unloaded = [u for u in new.unloaded if u.id != item.id]
    new.placed.append(
        unloaded_to_placed(
            item,
            x=req.x,
            y=req.y,
            z=req.z,
            dx=req.dx,
            dy=req.dy,
            dz=req.dz,
            orientation_label=orientation_label,
            tilt_angle=insert_tilt_angle,
            tilt_axis=insert_tilt_axis,
            base_dx=insert_base_dx,
            base_dy=insert_base_dy,
            base_dz=insert_base_dz,
        )
    )
    return new


def set_tilt(state: PlanState, req: SetTiltRequest) -> PlanState:
    """Fase 5C: cambiar el angulo de Tilt de una pieza ya colocada. Rechaza
    (CONFLICT) si la pieza no admite Tilt, el angulo excede el maximo
    efectivo, o la nueva geometria deja de ser valida -ver
    core/manual_move.py:validate_tilt_change, la unica fuente de verdad."""
    new = _copy(state)
    piece = find_placed(new, req.piece_id)
    _ensure_unlocked(piece)

    valid, reason, new_dims = validate_tilt_change(
        piece,
        req.tilt_angle,
        new.placed,
        new.load_space,
        reserved_zones_from_state(new.reserved_zones),
        new.clearance,
    )
    if not valid:
        raise conflict(reason)

    piece.x, piece.y, piece.z, piece.dx, piece.dy, piece.dz = new_dims
    piece.tilt_angle = req.tilt_angle
    return new


def _change_orientation(state: PlanState, piece_id: str, orientation_fn, action: str) -> PlanState:
    new = _copy(state)
    piece = find_placed(new, piece_id)
    _ensure_unlocked(piece)

    policy = piece.resolved_orientation_policy
    target = orientation_fn(piece.source_dimensions, piece.dx, piece.dy, piece.dz, policy)
    if target is None:
        raise conflict(f"Orientacion actual no reconocida, no se puede {action}")

    valid, reason = validate_placement(
        piece.id,
        piece.source_width,
        piece.source_height,
        piece.source_thickness,
        piece.stackable,
        piece.weight,
        piece.max_stack_weight,
        piece.x,
        piece.y,
        piece.z,
        target.dx,
        target.dy,
        target.dz,
        new.placed,
        new.load_space,
        reserved_zones_from_state(new.reserved_zones),
        new.clearance,
        policy,
    )
    if not valid:
        raise conflict(reason)

    piece.dx, piece.dy, piece.dz = target.dx, target.dy, target.dz
    piece.orientation_label = target.label
    return new


def rotate_piece(state: PlanState, piece_id: str) -> PlanState:
    """Siguiente orientacion valida (tecla R)."""
    return _change_orientation(state, piece_id, toggle_orientation, "rotar")


def turn_piece(state: PlanState, piece_id: str) -> PlanState:
    """Orientacion en el otro sentido (tecla T)."""
    return _change_orientation(state, piece_id, turn_orientation, "girar")


def _set_locked(state: PlanState, piece_id: str, locked: bool) -> PlanState:
    new = _copy(state)
    find_placed(new, piece_id).locked = locked
    return new


def lock_piece(state: PlanState, piece_id: str) -> PlanState:
    return _set_locked(state, piece_id, True)


def unlock_piece(state: PlanState, piece_id: str) -> PlanState:
    return _set_locked(state, piece_id, False)
