"""Zonas reservadas (seccion 9-10 de V2): ninguna pieza puede invadirlas.
El pasillo central se construye como una de estas zonas.

Bug fix (post-V4): el pasillo YA se calculaba centrado geometricamente
(central_aisle_zone), pero el motor de busqueda de candidatos solo tenia UN
punto de arranque (y=0) -asi que, aunque la ReservedZone en si estuviera bien
centrada, el algoritmo nunca generaba candidatos del lado lejano del pasillo
y terminaba metiendo toda la carga de un solo lado. El fix agrega un
candidato semilla justo despues de cada zona reservada."""

from app.core.geometry import Box
from app.core.manual_move import validate_placement
from app.core.optimize import run_optimization
from app.core.packer import pack_container
from app.core.reserved_zones import central_aisle_zone, zone_conflict, zone_conflict_with_clearance
from app.models.containers import get_container
from app.models.schemas import ContainerSpec, OptimizationMode, WeightBalanceMode, WindowItem


def _window(**overrides):
    defaults = dict(
        code="W1",
        description="Ventana test",
        width=800,
        height=1200,
        thickness=80,
        weight=20,
        quantity=40,
        system="SysA",
        group="G1",
        stackable=True,
        priority=1,
    )
    defaults.update(overrides)
    return WindowItem(**defaults)


def test_zone_conflict_detects_box_inside_zone():
    zone = central_aisle_zone(get_container("40ft_standard"), 500)
    box = Box("a", x=100, y=zone.y + 10, z=0, dx=100, dy=100, dz=100)
    assert zone_conflict(box, [zone]) is not None


def test_zone_conflict_none_when_outside():
    zone = central_aisle_zone(get_container("40ft_standard"), 500)
    box = Box("a", x=100, y=0, z=0, dx=100, dy=50, dz=100)
    assert zone_conflict(box, [zone]) is None


def test_pack_container_never_places_pieces_inside_aisle():
    container = get_container("40ft_standard")
    aisle = central_aisle_zone(container, 500)
    result = pack_container([_window()], container, OptimizationMode.BEST_SPACE, [aisle], 0.0)
    assert len(result.placed) > 0
    for p in result.placed:
        box = Box(p.id, p.x, p.y, p.z, p.dx, p.dy, p.dz, p.stackable)
        assert zone_conflict(box, [aisle]) is None, f"Pieza {p.id} invade el pasillo"


def test_central_aisle_is_geometrically_centered_on_container_width():
    """TEST OBLIGATORIO 1: con Container Width=2388mm y Aisle=500mm, el
    pasillo debe quedar exactamente centrado (944 | 500 | 944)."""
    container = ContainerSpec(id="t", name="t", length=10000, width=2388, height=2500, max_weight=100000)
    zone = central_aisle_zone(container, 500)
    assert zone.y == 944
    assert zone.width == 500
    left_cargo_width = zone.y
    right_cargo_width = container.width - (zone.y + zone.width)
    assert left_cargo_width == 944
    assert right_cargo_width == 944


def test_pack_container_with_central_aisle_uses_both_sides():
    """TEST OBLIGATORIO 2: con piezas chicas de sobra para los dos lados, el
    algoritmo debe generar placements validos tanto a la izquierda como a la
    derecha del pasillo -no solo de un lado (bug reportado)."""
    container = get_container("40ft_standard")  # width=2352
    aisle = central_aisle_zone(container, 500)  # 926 | 500 | 926
    small_window = _window(width=300, height=300, thickness=50, weight=5, quantity=60)

    result = pack_container([small_window], container, OptimizationMode.BEST_SPACE, [aisle], 0.0)

    left_pieces = [p for p in result.placed if p.y + p.dy <= aisle.y + 1e-6]
    right_pieces = [p for p in result.placed if p.y >= aisle.y + aisle.width - 1e-6]

    assert len(left_pieces) > 0, "no se genero ningun placement a la izquierda del pasillo"
    assert len(right_pieces) > 0, "no se genero ningun placement a la derecha del pasillo -bug: todo el cargo de un solo lado"


def test_central_aisle_respects_clearance_at_its_edge():
    """Minimum Clearance tambien debe respetarse contra el borde del
    pasillo, no solo entre piezas."""
    container = get_container("40ft_standard")
    aisle = central_aisle_zone(container, 500)
    clearance = 20
    # Pieza pegada (gap=0) al borde del pasillo: valida sin clearance...
    touching = Box("a", x=0, y=aisle.y - 100, z=0, dx=100, dy=100, dz=100)
    assert zone_conflict(touching, [aisle]) is None
    # ...pero invalida si se exige un clearance mayor que el gap real (0mm).
    assert zone_conflict_with_clearance(touching, [aisle], clearance) is not None


def test_weight_balance_important_with_central_aisle_uses_both_sides():
    """TEST OBLIGATORIO 4: con Weight Balance=Important y espacio valido en
    ambos lados, la mejor solucion no deberia cargar todo de un solo lado
    (no se exige 50/50 exacto)."""
    container = get_container("40ft_standard")
    zones = [central_aisle_zone(container, 500)]
    items = [_window(width=300, height=300, thickness=50, weight=10, quantity=80)]

    best, _ = run_optimization(items, container, OptimizationMode.BEST_SPACE, zones, 0.0, WeightBalanceMode.IMPORTANT)

    assert best.metrics.left_weight_kg > 0
    assert best.metrics.right_weight_kg > 0


def test_manual_insert_into_aisle_rejected():
    container = get_container("40ft_standard")
    aisle = central_aisle_zone(container, 500)
    valid, reason = validate_placement(
        "new-piece",
        800,
        1200,
        80,
        True,
        20,
        None,
        0,
        aisle.y + 10,
        0,
        800,
        80,
        1200,
        [],
        container,
        [aisle],
        0.0,
    )
    assert valid is False
    assert "reservada" in reason
