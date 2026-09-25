"""Integracion NAGSA, A1 (H5): services/piece_mapping.py.

Las conversiones antes vivian inline en api/routes.py. Estos tests fijan su
contrato sin pasar por HTTP: que campos se copian y, sobre todo, cuando se
"congelan" los overrides (Optimize Remaining) y cuando se conservan crudos
(quitar/insertar a mano)."""

from functools import lru_cache

from app.core.optimize import run_optimization
from app.core.reasons import UnloadedReason
from app.models.containers import get_container
from app.models.schemas import OptimizationMode, PlanHandlingRules, WeightBalanceMode, WindowItem
from app.services.piece_mapping import (
    MANUAL_REMOVE_REASON,
    placed_to_item,
    placed_to_unloaded,
    unloaded_to_item,
    unloaded_to_placed,
)

_IDENTITY_FIELDS = (
    "id", "code", "description", "system", "group", "weight", "stackable", "priority", "max_stack_weight",
    "delivery_sequence", "boxes_inside", "item_type", "orientation_policy", "stackable_override",
    "orientation_override", "allow_tilt", "max_tilt_angle",
)


@lru_cache(maxsize=1)
def _placed_piece():
    """Pieza real del optimizador con stackable heredado del plan (override
    None en el item) -el caso en que "congelar" vs "conservar crudo" difiere."""
    item = WindowItem(
        code="W1", description="Ventana", width=1200, height=2000, thickness=80, weight=40, quantity=1,
        item_type="panel", system="Picture Window", group="G1", priority=4, delivery_sequence=2,
    )
    best, _ = run_optimization(
        [item], get_container("20ft_standard"), OptimizationMode.BEST_SPACE, [], 0.0, WeightBalanceMode.NORMAL,
        plan_handling_rules=PlanHandlingRules(default_stackable=True),
    )
    (piece,) = best.placed
    return piece


def test_fixture_piece_inherits_stackable_from_plan():
    piece = _placed_piece()
    assert piece.stackable is True
    assert piece.stackable_override is None


def test_placed_to_item_freezes_resolved_values_as_overrides():
    piece = _placed_piece()
    item = placed_to_item(piece)
    assert item.quantity == 1
    assert (item.width, item.height, item.thickness) == (piece.source_width, piece.source_height, piece.source_thickness)
    assert item.stackable_override == piece.stackable  # congelado, no el None crudo
    assert item.orientation_override == piece.orientation_policy
    assert (item.code, item.group, item.system, item.priority, item.delivery_sequence) == (
        piece.code, piece.group, piece.system, piece.priority, piece.delivery_sequence,
    )


def test_placed_to_unloaded_keeps_raw_overrides_and_manual_reason():
    piece = _placed_piece()
    unloaded = placed_to_unloaded(piece)
    assert unloaded.stackable_override is None  # crudo: sigue heredando del plan
    assert unloaded.reason == MANUAL_REMOVE_REASON
    assert unloaded.reason_code == UnloadedReason.MANUAL_REMOVE.value
    assert (unloaded.width, unloaded.height, unloaded.thickness) == (
        piece.source_width, piece.source_height, piece.source_thickness,
    )
    for field in _IDENTITY_FIELDS:
        assert getattr(unloaded, field) == getattr(piece, field), field


def test_unloaded_to_item_freezes_resolved_values_as_overrides():
    unloaded = placed_to_unloaded(_placed_piece())
    item = unloaded_to_item(unloaded)
    assert item.quantity == 1
    assert item.stackable_override == unloaded.stackable
    assert item.orientation_override == unloaded.orientation_policy


def test_remove_then_insert_at_same_place_restores_the_piece():
    piece = _placed_piece()
    back = unloaded_to_placed(
        placed_to_unloaded(piece),
        x=piece.x, y=piece.y, z=piece.z, dx=piece.dx, dy=piece.dy, dz=piece.dz,
        orientation_label=piece.orientation_label, tilt_angle=piece.tilt_angle, tilt_axis=piece.tilt_axis,
        base_dx=piece.base_dx, base_dy=piece.base_dy, base_dz=piece.base_dz,
    )
    for field in _IDENTITY_FIELDS + ("x", "y", "z", "dx", "dy", "dz", "orientation_label", "source_width", "source_height", "source_thickness"):
        assert getattr(back, field) == getattr(piece, field), field
    assert back.locked is False
