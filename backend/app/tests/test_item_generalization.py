"""
CUBOX 2.0 - Fase 1: generalizacion del modelo de Item y del sistema de
orientacion.

La regla critica de ventanas (Test A) vive en test_orientation.py y no se
toca aca. Este archivo cubre los Tests B-G especificados para esta fase:
politicas de orientacion nuevas (FREE/UPRIGHT) y que el packer, la
validacion manual y la validacion final sigan siendo una unica fuente de
verdad consistente para cualquier tipo de item.
"""

from app.core.final_validation import validate_for_export
from app.core.manual_move import validate_placement
from app.core.orientation import get_valid_orientations, is_valid_orientation
from app.core.packer import compute_metrics, pack_container
from app.models.containers import get_container
from app.models.schemas import (
    ItemType,
    LoadItem,
    OrientationPolicy,
    PackingResult,
    PlacedPiece,
    WindowItem,
    dimensions_from_legacy,
)

CONTAINER = get_container("40ft_standard")


def test_window_item_is_load_item_alias_with_legacy_default():
    """WindowItem = LoadItem: un item importado sin item_type/orientation_policy
    debe comportarse exactamente como antes (PANEL / PANEL_EDGE_ONLY)."""
    item = WindowItem(code="w", width=1200, height=2000, thickness=100, weight=45, quantity=1)
    assert isinstance(item, LoadItem)
    assert item.item_type == ItemType.PANEL
    assert item.resolved_orientation_policy == OrientationPolicy.PANEL_EDGE_ONLY


# ---------------------------------------------------------------------------
# TEST B - FREE box: tres dimensiones distintas, las 6 orientaciones validas.
# ---------------------------------------------------------------------------


def test_free_box_has_six_orientations():
    orientations = get_valid_orientations(dimensions_from_legacy(600, 400, 300), OrientationPolicy.FREE)
    assert len(orientations) == 6
    combos = {(o.dx, o.dy, o.dz) for o in orientations}
    assert len(combos) == 6
    for dx, dy, dz in combos:
        assert {dx, dy, dz} == {600, 400, 300}


def test_free_box_accepts_any_axis_aligned_orientation():
    dims = dimensions_from_legacy(600, 400, 300)
    for dx, dy, dz in [(600, 400, 300), (400, 600, 300), (300, 400, 600), (400, 300, 600)]:
        assert is_valid_orientation(dims, dx, dy, dz, OrientationPolicy.FREE) is True


# ---------------------------------------------------------------------------
# TEST C - UPRIGHT box: `height` permanece siempre vertical.
# ---------------------------------------------------------------------------


def test_upright_box_has_two_floor_rotations():
    dims = dimensions_from_legacy(width=600, height=300, thickness=400)
    orientations = get_valid_orientations(dims, policy=OrientationPolicy.UPRIGHT)
    assert len(orientations) == 2
    for o in orientations:
        assert o.dz == 300
        assert {o.dx, o.dy} == {600, 400}


def test_upright_box_rejects_lying_on_its_side():
    dims = dimensions_from_legacy(600, 300, 400)
    assert is_valid_orientation(dims, dx=600, dy=400, dz=300, policy=OrientationPolicy.UPRIGHT) is True
    assert is_valid_orientation(dims, dx=400, dy=600, dz=300, policy=OrientationPolicy.UPRIGHT) is True
    assert is_valid_orientation(dims, dx=300, dy=400, dz=600, policy=OrientationPolicy.UPRIGHT) is False
    assert is_valid_orientation(dims, dx=400, dy=300, dz=600, policy=OrientationPolicy.UPRIGHT) is False


# ---------------------------------------------------------------------------
# TEST D - item tipo pallet: solo rotacion en el piso, nunca parado de canto.
# ---------------------------------------------------------------------------


def test_pallet_like_item_never_stands_on_edge():
    dims = dimensions_from_legacy(width=1200, height=150, thickness=1000)
    valid_orientations = get_valid_orientations(dims, policy=OrientationPolicy.UPRIGHT)
    assert len(valid_orientations) == 2
    for o in valid_orientations:
        assert o.dz == 150
        assert {o.dx, o.dy} == {1200, 1000}

    assert is_valid_orientation(dims, dx=1200, dy=1000, dz=150, policy=OrientationPolicy.UPRIGHT) is True
    assert is_valid_orientation(dims, dx=1000, dy=1200, dz=150, policy=OrientationPolicy.UPRIGHT) is True
    assert is_valid_orientation(dims, dx=150, dy=1000, dz=1200, policy=OrientationPolicy.UPRIGHT) is False
    assert is_valid_orientation(dims, dx=1000, dy=150, dz=1200, policy=OrientationPolicy.UPRIGHT) is False


# ---------------------------------------------------------------------------
# TEST E - el packer, corriendo con datos legacy de ventana, sigue
# respetando exactamente la misma regla de la cara de vidrio.
# ---------------------------------------------------------------------------


def test_packer_preserves_glass_face_rule_for_legacy_window_items():
    items = [WindowItem(code="W1", width=1200, height=2000, thickness=100, weight=45, quantity=3)]
    result = pack_container(items, CONTAINER, strategy="highest_priority")

    assert len(result.placed) == 3
    for p in result.placed:
        assert p.item_type == ItemType.PANEL
        assert p.resolved_orientation_policy == OrientationPolicy.PANEL_EDGE_ONLY
        assert p.dz != p.source_thickness
        assert is_valid_orientation(p.source_dimensions, p.dx, p.dy, p.dz, p.resolved_orientation_policy)


# ---------------------------------------------------------------------------
# TEST F - una orientacion rechazada por la politica activa tambien debe ser
# rechazada por el movimiento/insercion manual, y viceversa: lo que la
# politica permite para otro tipo de item, tambien lo permite el manual.
# ---------------------------------------------------------------------------


def test_manual_validation_rejects_glass_face_down_for_legacy_window():
    valid, reason = validate_placement(
        piece_id="w1",
        width=1200,
        height=2000,
        thickness=100,
        stackable=True,
        weight=45,
        max_stack_weight=None,
        x=0,
        y=0,
        z=0,
        dx=1200,
        dy=2000,
        dz=100,  # acostada sobre el vidrio
        other_pieces=[],
        container=CONTAINER,
    )
    assert valid is False
    assert "orientacion invalida" in reason.lower()


def test_manual_validation_orientation_policy_changes_the_verdict():
    """La misma terna (dx=600, dy=400, dz=300) es invalida bajo la politica
    de ventanas por defecto (dz coincide con thickness -cara de vidrio como
    base) pero valida para un item con OrientationPolicy.FREE."""
    kwargs = dict(
        piece_id="b1",
        width=600,
        height=400,
        thickness=300,
        stackable=True,
        weight=20,
        max_stack_weight=None,
        x=0,
        y=0,
        z=0,
        dx=600,
        dy=400,
        dz=300,
        other_pieces=[],
        container=CONTAINER,
    )
    valid_default, _ = validate_placement(**kwargs)
    valid_free, _ = validate_placement(**kwargs, orientation_policy=OrientationPolicy.FREE)

    assert valid_default is False
    assert valid_free is True


# ---------------------------------------------------------------------------
# TEST G - la validacion final detecta la misma orientacion invalida que la
# politica activa de cada pieza, sin marcar como invalido lo que su propia
# politica permite.
# ---------------------------------------------------------------------------


def _placed(**overrides) -> PlacedPiece:
    defaults = dict(
        id="a",
        code="a",
        weight=45,
        stackable=True,
        priority=1,
        max_stack_weight=None,
        x=0,
        y=0,
        z=0,
        dx=1200,
        dy=2000,
        dz=100,
        orientation_label="test",
        source_width=1200,
        source_height=2000,
        source_thickness=100,
    )
    defaults.update(overrides)
    return PlacedPiece(**defaults)


def test_final_validation_detects_invalid_orientation_for_legacy_window():
    piece = _placed()  # dz == source_thickness -> cara de vidrio como base
    metrics = compute_metrics(CONTAINER, [piece], [])
    result = PackingResult(container=CONTAINER, placed=[piece], unloaded=[], metrics=metrics)

    errors = validate_for_export(result, CONTAINER)
    assert any("orientacion invalida" in e for e in errors)


def test_final_validation_respects_free_policy_for_box_item():
    piece = _placed(
        id="b",
        code="b",
        weight=20,
        dx=600,
        dy=400,
        dz=300,
        source_width=600,
        source_height=400,
        source_thickness=300,
        item_type=ItemType.BOX,
        orientation_policy=OrientationPolicy.FREE,
    )
    metrics = compute_metrics(CONTAINER, [piece], [])
    result = PackingResult(container=CONTAINER, placed=[piece], unloaded=[], metrics=metrics)

    errors = validate_for_export(result, CONTAINER)
    assert not any("orientacion invalida" in e for e in errors)
