"""Validacion final antes de exportar PDF (CUBOX V4, prioridad 16): reusa los
chequeos existentes de geometry.py/orientation.py/reserved_zones.py, no
reimplementa reglas nuevas."""

from app.core.final_validation import validate_for_export
from app.core.packer import compute_metrics
from app.core.reserved_zones import ReservedZone
from app.models.containers import get_container
from app.models.schemas import PackingResult, PlacedPiece

CONTAINER = get_container("40ft_standard")


def _piece(piece_id, x, y=0, z=0, dx=100, dy=1200, dz=2000, weight=45, stackable=True, max_stack_weight=None):
    return PlacedPiece(
        id=piece_id,
        code=piece_id,
        weight=weight,
        stackable=stackable,
        priority=1,
        max_stack_weight=max_stack_weight,
        x=x,
        y=y,
        z=z,
        dx=dx,
        dy=dy,
        dz=dz,
        orientation_label="P1-a",
        source_width=1200,
        source_height=2000,
        source_thickness=100,
    )


def _result(placed: list[PlacedPiece]) -> PackingResult:
    metrics = compute_metrics(CONTAINER, placed, [])
    return PackingResult(container=CONTAINER, placed=placed, unloaded=[], metrics=metrics)


def test_valid_state_has_no_errors():
    placed = [_piece("a", x=0, y=0, z=0)]
    errors = validate_for_export(_result(placed), CONTAINER)
    assert errors == []


def test_detects_duplicated_ids():
    placed = [_piece("a", x=0, y=0), _piece("a", x=0, y=1300)]
    errors = validate_for_export(_result(placed), CONTAINER)
    assert any("duplicados" in e.lower() for e in errors)


def test_detects_out_of_bounds():
    placed = [_piece("a", x=CONTAINER.length - 10, y=0)]  # dx=100 -> se sale
    errors = validate_for_export(_result(placed), CONTAINER)
    assert any("fuera de los limites" in e for e in errors)


def test_detects_collision():
    placed = [_piece("a", x=0, y=0), _piece("b", x=0, y=0)]
    errors = validate_for_export(_result(placed), CONTAINER)
    assert any("Colision" in e for e in errors)


def test_detects_invalid_orientation():
    """dz == source_thickness -> la cara Width x Height quedo como base,
    exactamente la regla critica que nunca puede violarse."""
    placed = [_piece("a", x=0, y=0, dx=1200, dy=2000, dz=100)]
    errors = validate_for_export(_result(placed), CONTAINER)
    assert any("orientacion invalida" in e for e in errors)


def test_detects_insufficient_support():
    floating = _piece("a", x=0, y=0, z=500)  # nada debajo
    errors = validate_for_export(_result([floating]), CONTAINER)
    assert any("flotando" in e.lower() or "soporte" in e.lower() for e in errors)


def test_detects_stack_weight_exceeded():
    supporter = _piece("base", x=0, y=0, z=0, dx=100, dy=1200, dz=50, max_stack_weight=10)
    top = _piece("top", x=0, y=0, z=50, dx=100, dy=1200, dz=50, weight=45)
    errors = validate_for_export(_result([supporter, top]), CONTAINER)
    assert any("peso maximo apilable" in e.lower() for e in errors)


def test_detects_clearance_violation():
    a = _piece("a", x=0, y=0, z=0, dx=100, dy=100, dz=100)
    b = _piece("b", x=105, y=0, z=0, dx=100, dy=100, dz=100)  # 5mm de separacion
    errors = validate_for_export(_result([a, b]), CONTAINER, clearance=10)
    assert any("Clearance" in e for e in errors)


def test_detects_reserved_zone_conflict():
    zone = ReservedZone(x=0, y=0, z=0, length=CONTAINER.length, width=500, height=CONTAINER.height, label="aisle")
    inside_zone = _piece("a", x=0, y=100, z=0)  # dims default: valida y bien soportada, solo invade la zona
    errors = validate_for_export(_result([inside_zone]), CONTAINER, reserved_zones=[zone])
    assert errors == [f"a invade la zona reservada '{zone.label}'"]


def test_detects_total_weight_exceeded():
    placed = [_piece("a", x=0, y=0, weight=CONTAINER.max_weight + 1000)]
    errors = validate_for_export(_result(placed), CONTAINER)
    assert any("Peso total" in e for e in errors)
