from app.core.geometry import boxes_overlap, Box
from app.core.orientation import is_valid_orientation
from app.core.packer import _expand_instances, pack_container
from app.models.containers import get_container
from app.models.schemas import OptimizationMode, WindowItem


def make_window(**overrides):
    defaults = dict(
        code="W1",
        description="Test window",
        width=1200,
        height=2000,
        thickness=100,
        weight=45,
        quantity=1,
        system="SysA",
        group="G1",
        stackable=True,
        priority=1,
    )
    defaults.update(overrides)
    return WindowItem(**defaults)


def test_pack_single_piece_fits_and_valid_orientation():
    container = get_container("40ft_standard")
    result = pack_container([make_window(quantity=1)], container)
    assert result.metrics.loaded_pieces == 1
    piece = result.placed[0]
    assert is_valid_orientation(piece.source_dimensions, piece.dx, piece.dy, piece.dz)


def test_pack_never_produces_forbidden_orientation():
    container = get_container("40ft_standard")
    result = pack_container([make_window(quantity=30)], container)
    assert len(result.placed) > 0
    for piece in result.placed:
        assert is_valid_orientation(piece.source_dimensions, piece.dx, piece.dy, piece.dz)
        assert piece.dz != piece.source_thickness


def test_pack_no_collisions():
    container = get_container("40ft_standard")
    result = pack_container([make_window(quantity=50)], container)
    boxes = [Box(p.id, p.x, p.y, p.z, p.dx, p.dy, p.dz, p.stackable) for p in result.placed]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert not boxes_overlap(a, b), f"Colision entre {a.id} y {b.id}"


def test_pack_respects_weight_limit():
    container = get_container("20ft_standard")
    heavy = make_window(code="HEAVY", weight=container.max_weight, quantity=2)
    result = pack_container([heavy], container)
    assert result.metrics.total_weight <= container.max_weight
    assert result.metrics.unloaded_pieces >= 1


def test_pack_piece_too_big_goes_unloaded():
    container = get_container("20ft_standard")
    huge = make_window(code="HUGE", width=99999, height=99999, thickness=99999, quantity=1)
    result = pack_container([huge], container)
    assert result.metrics.loaded_pieces == 0
    assert result.metrics.unloaded_pieces == 1
    assert "no cabe" in result.unloaded[0].reason.lower()


def test_pack_non_stackable_has_nothing_on_top():
    container = get_container("40ft_standard")
    items = [make_window(code="BASE", stackable=False, quantity=20)]
    result = pack_container(items, container)
    for piece in result.placed:
        assert piece.z == 0


def test_pack_metrics_consistent():
    container = get_container("40ft_standard")
    result = pack_container([make_window(quantity=10)], container)
    assert result.metrics.total_pieces == 10
    assert result.metrics.loaded_pieces + result.metrics.unloaded_pieces == 10
    assert 0 <= result.metrics.used_volume_pct <= 100
    assert 0 <= result.metrics.weight_utilization_pct <= 100


def test_pack_all_within_container_bounds():
    container = get_container("40ft_high_cube")
    result = pack_container([make_window(quantity=40)], container)
    for p in result.placed:
        assert p.x >= 0 and p.y >= 0 and p.z >= 0
        assert p.x + p.dx <= container.length + 1e-6
        assert p.y + p.dy <= container.width + 1e-6
        assert p.z + p.dz <= container.height + 1e-6


def test_pack_fills_cross_section_from_the_back_without_a_single_long_row():
    """Sin pasillo, el cubicaje debe llenar una seccion transversal completa
    (ancho x alto) pegada a la pared del fondo antes de avanzar hacia la
    puerta, en vez de armar una sola fila larga a lo largo del contenedor."""
    container = get_container("40ft_standard")
    window = make_window(code="W1", width=800, height=1200, thickness=80, weight=15, quantity=40)
    result = pack_container([window], container)
    assert result.metrics.loaded_pieces == 40

    max_edge = max(p.x + p.dx for p in result.placed)
    min_edge = min(p.x for p in result.placed)
    span = max_edge - min_edge

    assert max_edge >= container.length - 1e-3, "Deberia quedar pegado a la pared del fondo (x=length)"
    assert span < container.length * 0.3, "No deberia extenderse en una sola fila larga a lo largo del contenedor"


def test_expand_instances_does_not_collide_ids_across_repeated_codes():
    """Regresion: el sufijo de id debe ser global por code en toda la lista,
    no reiniciarse en cada WindowItem -si dos entradas separadas comparten
    code (como pasa al reconstruir piezas de quantity=1 para Optimize
    Remaining), antes se generaban ids duplicados."""
    items = [make_window(code="W1", quantity=1) for _ in range(5)]
    instances = _expand_instances(items)
    ids = [inst.instance_id for inst in instances]
    assert len(ids) == len(set(ids)), f"ids duplicados: {ids}"
    assert ids == ["W1-001", "W1-002", "W1-003", "W1-004", "W1-005"]


def test_pack_with_grouping_by_group_runs():
    container = get_container("40ft_standard")
    items = [
        make_window(code="A", group="G1", quantity=5),
        make_window(code="B", group="G2", quantity=5),
    ]
    result = pack_container(items, container, optimization_mode=OptimizationMode.KEEP_GROUPS)
    assert result.metrics.loaded_pieces > 0
