"""Clearance / separacion minima entre piezas (seccion 11 de V2)."""

from app.core.geometry import Box, boxes_too_close, check_support, has_clearance_conflict
from app.core.manual_move import validate_placement
from app.core.packer import pack_container
from app.models.containers import get_container
from app.models.schemas import OptimizationMode, WindowItem


def test_boxes_too_close_below_clearance():
    a = Box("a", 0, 0, 0, 100, 100, 100)
    b = Box("b", 105, 0, 0, 100, 100, 100)  # 5mm de separacion en X
    assert boxes_too_close(a, b, clearance=10) is True


def test_boxes_ok_at_or_above_clearance():
    a = Box("a", 0, 0, 0, 100, 100, 100)
    b = Box("b", 110, 0, 0, 100, 100, 100)  # exactamente 10mm
    assert boxes_too_close(a, b, clearance=10) is False


def test_clearance_ignored_when_zero():
    a = Box("a", 0, 0, 0, 100, 100, 100)
    b = Box("b", 101, 0, 0, 100, 100, 100)
    assert boxes_too_close(a, b, clearance=0) is False


def test_clearance_does_not_apply_between_stacked_levels():
    bottom = Box("bottom", 0, 0, 0, 100, 100, 100)
    top = Box("top", 0, 0, 100, 100, 100, 100)  # apoyada justo encima, mismo XY
    assert boxes_too_close(bottom, top, clearance=50) is False


def test_has_clearance_conflict_finds_offending_box():
    a = Box("a", 0, 0, 0, 100, 100, 100)
    others = [Box("b", 103, 0, 0, 100, 100, 100)]
    conflict = has_clearance_conflict(a, others, clearance=10)
    assert conflict is not None
    assert conflict.id == "b"


def test_check_support_and_clearance_combined():
    """CUBOX V4 (auditoria Stackable, prioridad 10): soporte (que hay debajo)
    y clearance (que hay al lado, al mismo nivel) son chequeos independientes
    -una pieza bien soportada puede seguir violando clearance contra un
    vecino, y eso no debe confundir a ninguno de los dos chequeos."""
    floor = Box("floor", 0, 0, 0, 200, 200, 50, stackable=True)
    top = Box("top", 0, 0, 50, 200, 200, 50, stackable=True)  # 100% soportada por floor
    neighbor = Box("neighbor", 205, 0, 50, 50, 50, 50, stackable=True)  # mismo nivel Z, 5mm de separacion

    support_ok, support_reason = check_support(top, [floor, neighbor])
    assert support_ok is True, support_reason

    assert boxes_too_close(top, neighbor, clearance=10) is True
    assert boxes_too_close(top, neighbor, clearance=3) is False


def test_manual_move_rejected_when_violates_clearance():
    container = get_container("40ft_standard")
    other = [
        _placed_piece("a", x=0, y=0)
    ]
    valid, reason = validate_placement(
        "b",
        1200,
        2000,
        100,
        True,
        45,
        None,
        1205,  # solo 5mm de separacion de "a" (que ocupa x:[0,1200])
        0,
        0,
        1200,
        100,
        2000,
        other,
        container,
        [],
        20.0,  # clearance minimo = 20mm
    )
    assert valid is False
    assert "separacion" in reason


def test_pack_container_with_clearance_still_places_multiple_pieces():
    """Regresion: el candidato generado junto a una pieza (gap=0) siempre
    violaba el clearance, dejando al algoritmo colocar practicamente 1 sola
    pieza. Los candidatos deben generarse ya separados por el clearance."""
    container = get_container("40ft_standard")
    window = WindowItem(
        code="W1", width=800, height=1200, thickness=80, weight=20, quantity=20,
        system="SysA", group="G1", stackable=True, priority=1,
    )
    result = pack_container([window], container, OptimizationMode.BEST_SPACE, [], 20.0)
    assert result.metrics.loaded_pieces >= 10, "El clearance no deberia bloquear casi todas las colocaciones"

    boxes = [Box(p.id, p.x, p.y, p.z, p.dx, p.dy, p.dz, p.stackable) for p in result.placed]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert not boxes_too_close(a, b, clearance=20.0), f"{a.id} y {b.id} violan el clearance"


def _placed_piece(piece_id, x, y):
    from app.models.schemas import PlacedPiece

    return PlacedPiece(
        id=piece_id,
        code=piece_id,
        weight=45,
        stackable=True,
        priority=1,
        x=x,
        y=y,
        z=0,
        dx=1200,
        dy=100,
        dz=2000,
        orientation_label="P1-a",
        source_width=1200,
        source_height=2000,
        source_thickness=100,
    )
